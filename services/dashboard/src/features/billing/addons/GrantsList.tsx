import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { Grant } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/format/date";
import type { Currency } from "@/lib/format/money";
import { addOnStrings as s } from "@/lib/strings/billing-addons";
import { fill } from "@/lib/strings/en";
import { Panel } from "../../kbs/ui";
import { ApplyAddOnDialog } from "./ApplyAddOnDialog";
import { grantsQuery, useRevokeGrant } from "./api";
import { grantMoney } from "./summary";

const t = s.grants;

function statusOf(g: Grant): ReactNode {
	if (g.revoked_at) return <Badge variant="outline">{fill(t.revokedOn, { date: formatDate(g.revoked_at) })}</Badge>;
	if (g.kind === "credit_pack") return <Badge variant="info">{t.creditGiven}</Badge>;
	return g.active ? <Badge variant="ok">{t.ongoing}</Badge> : <Badge variant="outline">{t.ended}</Badge>;
}

/**
 * A customer's add-ons (BIL5): what is active now (revoke one with a confirmation), the history
 * below it, and "Apply an add-on". Credit packs live in the history: they changed the balance,
 * not a limit, and cannot be revoked (the money is corrected with an adjustment).
 */
export function GrantsList({
	tenantId,
	tenantName,
	currency,
}: {
	tenantId: string;
	tenantName: string;
	currency: Currency;
}) {
	const q = useQuery(grantsQuery(tenantId));
	const [applying, setApplying] = useState(false);
	const [revoking, setRevoking] = useState<Grant | null>(null);

	const columns = (withRevoke: boolean): Column<Grant>[] => [
		{
			id: "name",
			header: t.columns.name,
			rowHeader: true,
			stackLabel: true,
			cell: (g) => (
				<div className="grid gap-0.5">
					<span className="font-semibold">{g.name}</span>
					{g.memo ? <small className="text-[12px] text-muted-foreground">{g.memo}</small> : null}
				</div>
			),
		},
		{ id: "what", header: t.columns.what, stackLabel: true, cell: (g) => g.summary },
		{
			id: "paid",
			header: t.columns.paid,
			stackLabel: true,
			cell: (g) => {
				const money = grantMoney(g);
				return money && g.payment === "wallet" ? `${t.paid.wallet}: ${money}` : t.paid[g.payment];
			},
		},
		{
			id: "given",
			header: t.columns.given,
			stackLabel: true,
			cell: (g) => (
				<span className="whitespace-nowrap">
					{formatDateTime(g.created_at)}
					<small className="block text-[12px] text-muted-foreground">{fill(t.by, { who: g.granted_by })}</small>
				</span>
			),
		},
		{
			id: "ends",
			header: t.columns.ends,
			stackLabel: true,
			cell: (g) =>
				g.kind === "credit_pack" || (g.kind === "service" && !g.ends_at)
					? "—"
					: g.ends_at
						? formatDate(g.ends_at)
						: t.endsNever, // P5 plans: a one-time service has no end
		},
		{ id: "status", header: t.columns.status, stackLabel: true, cell: statusOf },
		...(withRevoke
			? [
					{
						id: "actions",
						header: "",
						align: "right" as const,
						cell: (g: Grant) => (
							<Button type="button" size="sm" variant="ghost" onClick={() => setRevoking(g)}>
								{t.revoke}
							</Button>
						),
					},
				]
			: []),
	];

	let body: ReactNode;
	if (q.isPending) body = <TableSkeleton rows={3} />;
	else if (q.isError)
		body = (
			<div className="p-4">
				<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
			</div>
		);
	else if (q.data.items.length === 0)
		body = (
			<p data-testid="grants-empty" className="m-0 p-6 text-foreground-2">
				{t.none}
			</p>
		);
	else {
		const active = q.data.items.filter((g) => g.active);
		const history = q.data.items.filter((g) => !g.active);
		body = (
			<div className="grid gap-4 py-2">
				<section aria-label={t.active} className="grid gap-2">
					<h3 className="px-4 text-[13px] font-bold">{t.active}</h3>
					{active.length === 0 ? (
						<p className="m-0 px-4 text-[13px] text-muted-foreground">{t.noneActive}</p>
					) : (
						<div className="overflow-x-auto" data-testid="grants-active">
							<DataTable caption={t.activeCaption} columns={columns(true)} rows={active} rowKey={(g) => g.id} />
						</div>
					)}
				</section>
				{history.length > 0 ? (
					<section aria-label={t.history} className="grid gap-2">
						<h3 className="px-4 text-[13px] font-bold">{t.history}</h3>
						<div className="overflow-x-auto" data-testid="grants-history">
							<DataTable caption={t.historyCaption} columns={columns(false)} rows={history} rowKey={(g) => g.id} />
						</div>
					</section>
				) : null}
			</div>
		);
	}

	return (
		<Panel
			title={t.title}
			sub={t.lead}
			aside={
				<Button type="button" size="sm" variant="default" onClick={() => setApplying(true)}>
					<Plus aria-hidden className="size-4" />
					{s.apply.button}
				</Button>
			}
		>
			<div data-testid="customer-add-ons">{body}</div>
			<ApplyAddOnDialog
				open={applying}
				onOpenChange={setApplying}
				tenantId={tenantId}
				tenantName={tenantName}
				currency={currency}
			/>
			{revoking ? <RevokeDialog tenantId={tenantId} grant={revoking} onClose={() => setRevoking(null)} /> : null}
		</Panel>
	);
}

function RevokeDialog({ tenantId, grant, onClose }: { tenantId: string; grant: Grant; onClose: () => void }) {
	const id = useId();
	const revoke = useRevokeGrant(tenantId);
	const [memo, setMemo] = useState("");
	const [failure, setFailure] = useState<string | null>(null);
	const submit = async () => {
		setFailure(null);
		try {
			await revoke.mutateAsync({ grantId: grant.id, memo: memo.trim() || undefined });
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
			return;
		}
		toast.success(t.revokedToast, { description: fill(t.revokedBody, { name: grant.name }) });
		onClose();
	};
	return (
		<ConfirmDialog
			open
			onOpenChange={(o) => {
				if (!o) onClose();
			}}
			title={fill(t.revokeTitle, { name: grant.name })}
			body={t.revokeBody}
			confirmLabel={t.revokeConfirm}
			pending={revoke.isPending}
			onConfirm={() => void submit()}
		>
			<div className="grid gap-1.5 px-0 pb-2">
				<label htmlFor={id} className="text-[13px] font-semibold">
					{t.revokeNote}
				</label>
				<Textarea id={id} rows={2} maxLength={500} value={memo} onChange={(e) => setMemo(e.target.value)} />
				{failure ? (
					<small role="alert" className="text-[12.5px] font-medium text-destructive">
						{failure}
					</small>
				) : null}
			</div>
		</ConfirmDialog>
	);
}
