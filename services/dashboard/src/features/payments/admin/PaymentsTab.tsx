import { useQuery } from "@tanstack/react-query";
import { Link2 } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { toast } from "sonner";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date";
import { formatMinor } from "@/lib/format/money";
import { en, fill } from "@/lib/strings/en";
import { paymentStrings } from "@/lib/strings/payments";
import {
	adminPaymentsQuery,
	PAYMENTS_PAGE,
	type PaymentEvent,
	type PaymentRow,
	type PaymentStatus,
	paymentDetailQuery,
	paymentEventsQuery,
	useRetryEvent,
} from "../api";
import { PaymentLinkDialog } from "./PaymentLinkDialog";
import { PaymentSettingsCard } from "./PaymentSettingsCard";
import { RefundDialog } from "./RefundDialog";

const s = paymentStrings.admin;
const money = (n: number) => formatMinor(n, "INR");
const STATUSES: PaymentStatus[] = ["captured", "partially_refunded", "refunded", "authorized", "failed", "mismatch"];

function statusBadge(status: PaymentStatus): ReactNode {
	const tone = status === "captured" ? "ok" : status === "mismatch" || status === "failed" ? "bad" : "default";
	return <Badge variant={tone}>{s.table.status[status]}</Badge>;
}

/** Billing → Payments (P1): the payment service settings, payments received, and what needs attention. */
export function PaymentsTab() {
	return (
		<div className="grid gap-6" data-testid="billing-payments">
			<PaymentSettingsCard />
			<PaymentsTable />
			<FailedEvents />
		</div>
	);
}

function PaymentsTable() {
	const filterId = useId();
	const [status, setStatus] = useState<PaymentStatus | "">("");
	const [page, setPage] = useState(1);
	const [openId, setOpenId] = useState<string | null>(null);
	const [linking, setLinking] = useState(false);
	const q = useQuery(adminPaymentsQuery({ status: status || undefined, offset: (page - 1) * PAYMENTS_PAGE }));
	const rows = q.data?.items ?? [];

	const columns: Column<PaymentRow>[] = [
		{
			id: "date",
			header: s.table.columns.date,
			rowHeader: true,
			sortValue: (r) => r.created_at,
			cell: (r) => formatDate(r.captured_at ?? r.created_at),
		},
		{ id: "workspace", header: s.table.columns.workspace, stackLabel: true, cell: (r) => r.tenant_name },
		{ id: "what", header: s.table.columns.what, stackLabel: true, cell: (r) => r.description },
		{
			id: "amount",
			header: s.table.columns.amount,
			stackLabel: true,
			align: "right",
			cell: (r) => <span className="font-mono tabular-nums">{money(r.gross_minor)}</span>,
		},
		{ id: "status", header: s.table.columns.status, stackLabel: true, cell: (r) => statusBadge(r.status) },
		{
			id: "fee",
			header: s.table.columns.fee,
			stackLabel: true,
			align: "right",
			cell: (r) => (
				<span className="font-mono tabular-nums">
					{r.fee_minor == null ? "—" : money(r.fee_minor - (r.fee_tax_minor ?? 0))}
				</span>
			),
		},
		{
			id: "open",
			header: "",
			cell: (r) => (
				<Button type="button" variant="ghost" size="sm" onClick={() => setOpenId(r.id)}>
					{s.table.open}
				</Button>
			),
		},
	];

	let body: ReactNode;
	if (q.isPending) body = <TableSkeleton rows={4} />;
	else if (q.isError)
		body = (
			<div className="p-4">
				<ErrorState error={q.error} title={s.table.loadError} onRetry={() => void q.refetch()} />
			</div>
		);
	else if (rows.length === 0)
		body = (
			<p data-testid="payments-table-empty" className="m-0 p-6 text-foreground-2">
				{status ? s.table.emptyFiltered : s.table.empty}
			</p>
		);
	else
		body = (
			<>
				<DataTable caption={s.table.caption} columns={columns} rows={rows} rowKey={(r) => r.id} />
				{(q.data?.total ?? 0) > PAYMENTS_PAGE ? (
					<Pager page={page} pageSize={PAYMENTS_PAGE} total={q.data?.total ?? 0} onPage={setPage} />
				) : null}
			</>
		);

	return (
		<section
			aria-label={s.table.title}
			className="min-w-0 rounded-lg border border-border bg-card"
			data-testid="payments-table"
		>
			<div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3.5">
				<h2 className="text-[15px] font-bold">{s.table.title}</h2>
				<div className="flex flex-wrap items-center gap-3">
					<label htmlFor={filterId} className="sr-only">
						{s.table.columns.status}
					</label>
					<NativeSelect
						id={filterId}
						value={status}
						onChange={(e) => {
							setStatus(e.target.value as PaymentStatus | "");
							setPage(1);
						}}
					>
						<option value="">{s.table.allStatuses}</option>
						{STATUSES.map((st) => (
							<option key={st} value={st}>
								{s.table.status[st]}
							</option>
						))}
					</NativeSelect>
					<Button type="button" variant="outline" onClick={() => setLinking(true)}>
						<Link2 aria-hidden className="size-4" />
						{s.link.button}
					</Button>
				</div>
			</div>
			{body}
			{openId ? <PaymentDetailDialog id={openId} onClose={() => setOpenId(null)} /> : null}
			<PaymentLinkDialog open={linking} onOpenChange={setLinking} />
		</section>
	);
}

function PaymentDetailDialog({ id, onClose }: { id: string; onClose: () => void }) {
	const q = useQuery(paymentDetailQuery(id));
	const [refunding, setRefunding] = useState(false);
	const d = q.data;
	return (
		<Dialog open onOpenChange={(o) => !o && onClose()}>
			<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[640px]" data-testid="payment-detail">
				<DialogHeader>
					<DialogTitle>{s.detail.title}</DialogTitle>
					<DialogDescription>{d ? `${d.tenant_name} · ${d.description}` : en.common.loading}</DialogDescription>
				</DialogHeader>
				{q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
				{d ? (
					<div className="grid gap-4">
						<KeyValue
							rows={[
								{ label: s.detail.amount, value: money(d.gross_minor) },
								{ label: s.detail.taxable, value: money(d.taxable_minor) },
								{ label: s.detail.tax, value: money(d.tax_minor) },
								{ label: s.detail.fee, value: d.fee_minor == null ? "—" : money(d.fee_minor - (d.fee_tax_minor ?? 0)) },
								{ label: s.detail.feeTax, value: d.fee_tax_minor == null ? "—" : money(d.fee_tax_minor) },
								{ label: s.detail.method, value: d.method ?? "—" },
								{ label: s.detail.reference, value: <code>{d.provider_payment_id}</code> },
								{ label: s.detail.settled, value: d.settled_at ? formatDate(d.settled_at) : s.detail.notSettled },
								{
									label: s.detail.refundable,
									value: <span data-testid="refundable">{money(d.refundable_minor)}</span>,
								},
							]}
						/>
						{d.refund_deadline ? (
							<p className="m-0 text-[12.5px] text-muted-foreground">
								{fill(s.detail.deadline, { date: formatDate(d.refund_deadline) })}
							</p>
						) : null}
						<div>
							<h3 className="mb-1 text-[13px] font-bold">{s.detail.refunds}</h3>
							{d.refunds.length === 0 ? (
								<p className="m-0 text-[13px] text-muted-foreground">{s.detail.noRefunds}</p>
							) : (
								<ul className="m-0 list-none p-0 text-[13px]">
									{d.refunds.map((r) => (
										<li key={r.id} className="flex flex-wrap justify-between gap-2 border-b border-border py-1.5">
											<span>
												{formatDate(r.created_at)} · {r.memo}
											</span>
											<span className="font-mono">
												{money(r.gross_minor)} · {r.status}
											</span>
										</li>
									))}
								</ul>
							)}
						</div>
						<div>
							<h3 className="mb-1 text-[13px] font-bold">{s.detail.events}</h3>
							{d.events.length === 0 ? (
								<p className="m-0 text-[13px] text-muted-foreground">{s.detail.noEvents}</p>
							) : (
								<ul className="m-0 list-none p-0 text-[13px]">
									{d.events.map((e) => (
										<li key={e.id} className="flex flex-wrap justify-between gap-2 border-b border-border py-1.5">
											<code>{e.event}</code>
											<span>{e.status}</span>
										</li>
									))}
								</ul>
							)}
						</div>
						<p className="m-0 text-[12.5px] text-muted-foreground">{s.detail.creditNote}</p>
						<div className="flex justify-end">
							<Button
								type="button"
								variant="default"
								disabled={d.refundable_minor <= 0}
								onClick={() => setRefunding(true)}
							>
								{s.detail.refund}
							</Button>
						</div>
						<RefundDialog payment={d} open={refunding} onOpenChange={setRefunding} />
					</div>
				) : null}
			</DialogContent>
		</Dialog>
	);
}

function FailedEvents() {
	const q = useQuery(paymentEventsQuery);
	const retry = useRetryEvent();
	const rows: PaymentEvent[] = q.data?.items ?? [];

	const columns: Column<PaymentEvent>[] = [
		{ id: "when", header: s.events.columns.when, rowHeader: true, cell: (r) => formatDate(r.received_at) },
		{ id: "event", header: s.events.columns.event, stackLabel: true, cell: (r) => <code>{r.event}</code> },
		{ id: "status", header: s.events.columns.status, stackLabel: true, cell: (r) => r.status },
		{ id: "tries", header: s.events.columns.tries, stackLabel: true, align: "right", cell: (r) => r.attempts },
		{ id: "problem", header: s.events.columns.problem, stackLabel: true, cell: (r) => r.error ?? "—" },
		{
			id: "retry",
			header: "",
			cell: (r) => (
				<Button
					type="button"
					variant="ghost"
					size="sm"
					disabled={retry.isPending}
					onClick={async () => {
						try {
							await retry.mutateAsync(r.id);
							toast.success(s.events.queued);
						} catch (e) {
							toast.error(messageFor(toApiError(e)).title);
						}
					}}
				>
					{s.events.retry}
				</Button>
			),
		},
	];

	return (
		<section
			aria-label={s.events.title}
			className="min-w-0 rounded-lg border border-border bg-card"
			data-testid="payment-events"
		>
			<div className="border-b border-border px-4 py-3.5">
				<h2 className="text-[15px] font-bold">{s.events.title}</h2>
				<div className="text-[12.5px] text-muted-foreground">{s.events.lead}</div>
			</div>
			{q.isPending ? (
				<TableSkeleton rows={2} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={s.events.loadError} onRetry={() => void q.refetch()} />
				</div>
			) : rows.length === 0 ? (
				<p data-testid="payment-events-empty" className="m-0 p-4 text-[13px] text-muted-foreground">
					{s.events.empty}
				</p>
			) : (
				<DataTable caption={s.events.title} columns={columns} rows={rows} rowKey={(r) => r.id} />
			)}
		</section>
	);
}
