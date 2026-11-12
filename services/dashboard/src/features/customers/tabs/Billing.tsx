import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Textarea } from "@/components/ui/textarea";
import { AccountPanel } from "@/features/billing/accounts/AccountPanel";
import { LimitsPanel } from "@/features/billing/accounts/LimitsPanel";
import { PendingChange } from "@/features/billing/accounts/PendingChange"; // TV3 notice
import { TermsPanel } from "@/features/billing/accounts/TermsPanel";
import { GrantsList } from "@/features/billing/addons/GrantsList"; // BIL5 add-ons
import { billingAccountQuery, useUpdateBillingAccount } from "@/features/billing/api";
import { TransactionsTable } from "@/features/billing/ledger/TransactionsTable"; // BIL3 ledger
import { StatementsPanel } from "@/features/billing/statements/StatementsPanel"; // BIL6 statements
import { FormFoot, Panel } from "@/features/kbs/ui";
import type { AdminTenant } from "@/lib/api/accounts-types";
import type { BillingAccount } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date";
import { customerBillingStrings as t } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";

/**
 * Billing (BIL2): the customer's billing account. The terms they pay with, a "Custom" switch per
 * field, their limits, the currency, billing day, pausing and notes. The operator's own workspace
 * is never billed: it shows one line and a notes box.
 */
export function BillingTab({ tenant }: { tenant: AdminTenant }) {
	const q = useQuery(billingAccountQuery(tenant.id));
	if (q.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<CardSkeleton lines={3} />
				<CardSkeleton lines={6} />
			</div>
		);
	}
	if (q.isError) return <ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />;
	const account = q.data;
	// Remount the editors when the server's copy changes, so they start from what was saved.
	const stamp = `${account.updated_at ?? ""}:${account.pricing_version ?? 0}`;
	return (
		<div className="grid items-start gap-4" data-testid="customer-billing">
			<SummaryPanel account={account} />
			<PendingChange account={account} /> {/* TV3 notice */}
			{account.exempt ? (
				<ExemptPanel key={stamp} account={account} />
			) : (
				<>
					{/* BIL3 ledger */}
					<TransactionsTable
						tenantId={account.tenant_id}
						tenantName={account.tenant_name}
						currency={account.currency}
					/>
					{/* /BIL3 ledger */}
					{/* BIL6 statements */}
					<StatementsPanel tenantId={account.tenant_id} tenantName={account.tenant_name} />
					{/* /BIL6 statements */}
					{/* BIL5 add-ons */}
					<GrantsList tenantId={account.tenant_id} tenantName={account.tenant_name} currency={account.currency} />
					{/* /BIL5 add-ons */}
					<TermsPanel key={`terms-${stamp}`} account={account} />
					<LimitsPanel key={`limits-${stamp}`} account={account} />
					<AccountPanel key={`account-${stamp}`} account={account} />
				</>
			)}
		</div>
	);
}

function SummaryPanel({ account }: { account: BillingAccount }) {
	const rows = [
		{ label: t.summary.plan, value: account.plan.name },
		{ label: t.summary.currency, value: account.currency },
		{
			label: t.summary.pricing,
			value:
				account.pricing_version == null
					? t.summary.noPricing
					: fill(t.summary.pricingVersion, { n: String(account.pricing_version) }),
		},
		...(account.trial_ends_at ? [{ label: t.summary.trial, value: formatDate(account.trial_ends_at) }] : []),
		// BIL4 state
		{ label: t.summary.answers, value: answersText(account) },
		// /BIL4 state
	];
	return (
		<Panel title={t.summary.title} sub={account.exempt ? t.exempt : undefined}>
			<KeyValue rows={rows} />
		</Panel>
	);
}

// BIL4 state
function answersText(account: BillingAccount): string {
	const v = t.summary.answersValue;
	let text: string = v.active;
	if (account.status === "paused") text = account.pause_reason === "credit_limit" ? v.credit_limit : v.paused;
	else if (account.status === "forced") text = v.forced;
	else if (account.status === "low") text = v.low;
	else if (account.status === "not_billed") text = v.not_billed;
	// What the override does, next to the state it produces.
	if (account.pause_override === "never") return `${text}. ${t.summary.overrideNever}`;
	if (account.pause_override === "force") return `${text}. ${t.summary.overrideForce}`;
	return text;
}
// /BIL4 state

/** The operator's own workspace (and the Internal plan): never billed; only the note can change. */
function ExemptPanel({ account }: { account: BillingAccount }) {
	const id = useId();
	const update = useUpdateBillingAccount(account.tenant_id);
	const [notes, setNotes] = useState(account.notes ?? "");
	const [failure, setFailure] = useState<string | null>(null);
	const save = async () => {
		setFailure(null);
		try {
			await update.mutateAsync({ notes: notes.trim() });
			toast.success(t.account.saved, { description: fill(t.account.savedBody, { name: account.tenant_name }) });
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
		}
	};
	return (
		<form
			onSubmit={(e) => {
				e.preventDefault();
				void save();
			}}
			noValidate
		>
			<Panel title={t.exempt} sub={t.exemptBody}>
				<div className="grid gap-4 p-4">
					{failure ? <FormAlert>{failure}</FormAlert> : null}
					<Field id={id} label={t.account.notes} hint={t.account.notesHint}>
						<Textarea id={id} rows={3} value={notes} onChange={(e) => setNotes(e.target.value)} />
					</Field>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={update.isPending} pendingLabel={t.account.saving}>
						{t.account.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
