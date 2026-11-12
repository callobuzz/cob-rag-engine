import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { AlertTriangle, Wallet, X } from "lucide-react";
import { useState } from "react";
import { buttonVariants } from "@/components/ui/button";
import { ownBillingQuery } from "@/features/billing/addons/ActiveAddOns";
import { fairUseMineQuery } from "@/features/fair-use/api"; // PIP4
import { usePrincipal } from "@/lib/auth/store";
import { formatMinor } from "@/lib/format/money";
import { billingBannerStrings as t } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";

const DISMISS_KEY = "cob.billing-low-dismissed";

function dismissedThisSession(): boolean {
	try {
		return window.sessionStorage.getItem(DISMISS_KEY) === "1";
	} catch {
		return false;
	}
}

/**
 * Billing banner (BIL4), a workspace's own view only (never the operator's): answers are paused
 * because the balance ran out, or the balance is low. Money is shown only when the plan lets this
 * person see it; a member is told to ask the owner and gets no link or amount. Only the "low"
 * notice can be dismissed, and only for the session; a pause stays until it is fixed.
 */
export function BillingBanner() {
	const principal = usePrincipal();
	const inWorkspace = principal?.view === "tenant" && !principal.tenant?.isOperator;
	const q = useQuery({ ...ownBillingQuery, enabled: inWorkspace });
	const [dismissed, setDismissed] = useState(dismissedThisSession);
	// PIP4: when an empty balance only slows answers down, the fair-use banner says so instead
	const fair = useQuery({ ...fairUseMineQuery, enabled: inWorkspace && q.data?.paused === true });
	const slowedOnly = fair.data?.billing_block === "downgrade_profile";
	// /PIP4
	const account = q.data;
	if (!inWorkspace || !account || account.exempt) return null;
	const isMember = principal?.role === "member";

	if (account.paused) {
		if (slowedOnly) return null; // PIP4
		const text = isMember
			? t.pausedMember
			: account.pause_reason === "forced"
				? t.pausedForced
				: account.pause_reason === "credit_limit"
					? t.pausedCredit
					: t.paused;
		return (
			<div
				role="alert"
				aria-label={t.region}
				data-testid="billing-banner-paused"
				className={cn(
					"flex flex-wrap items-start gap-3 border-b px-4 py-2.5 text-[13px] text-foreground min-[861px]:px-7",
					"border-[color-mix(in_srgb,var(--destructive)_35%,var(--border))] bg-destructive-soft",
				)}
			>
				<AlertTriangle className="mt-0.5 size-4 shrink-0 text-destructive" aria-hidden />
				<p className="m-0 min-w-0 flex-1">{text}</p>
				{isMember ? null : (
					<Link to="/plan" className={buttonVariants({ size: "sm", variant: "outline" })}>
						{t.seePlan}
					</Link>
				)}
			</div>
		);
	}

	const low = account.alert_level != null && account.alert_level > 0;
	if (!low || isMember || dismissed) return null;
	// A postpaid account's level is measured on the credit still free: limit + available.
	const postpaid = account.payment_type === "postpaid" && account.credit_limit_minor != null;
	const left =
		account.available_minor == null
			? null
			: postpaid
				? account.available_minor + (account.credit_limit_minor ?? 0)
				: account.available_minor;
	const amount = account.money_visible && left != null ? formatMinor(left, account.currency) : null;
	return (
		<div
			role="status"
			aria-label={t.region}
			data-testid="billing-banner-low"
			className="flex flex-wrap items-start gap-3 border-b border-[color-mix(in_srgb,var(--warning)_35%,var(--border))] bg-warning-soft px-4 py-2.5 text-[13px] text-foreground min-[861px]:px-7"
		>
			<Wallet className="mt-0.5 size-4 shrink-0 text-warning" aria-hidden />
			<p className="m-0 min-w-0 flex-1">
				{amount ? fill(postpaid ? t.lowCreditAmount : t.lowAmount, { amount }) : t.low}
			</p>
			<Link to="/plan" className={buttonVariants({ size: "sm", variant: "outline" })}>
				{t.seePlan}
			</Link>
			<button
				type="button"
				aria-label={t.dismiss}
				className="rounded-sm p-1 text-foreground-2 hover:text-foreground"
				onClick={() => {
					try {
						window.sessionStorage.setItem(DISMISS_KEY, "1");
					} catch {
						// storage blocked: dismiss for this render only
					}
					setDismissed(true);
				}}
			>
				<X className="size-4" aria-hidden />
			</button>
		</div>
	);
}
