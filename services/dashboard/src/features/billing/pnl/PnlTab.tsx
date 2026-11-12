import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useId, useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { downloadText } from "@/features/usage/csv";
import type { PnlReport, PnlRow } from "@/lib/api/billing-types";
import { type Currency, formatMinor } from "@/lib/format/money";
import { formatNumber } from "@/lib/format/number";
import { pnlStrings as s } from "@/lib/strings/billing-statements";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { Panel } from "../../kbs/ui";
import { MAX_PNL_MONTHS, monthOf, monthsBetween, pnlQuery } from "./api";
import { pnlCsv } from "./csv";

const money = (minor: number, currency: Currency) => (
	<span className="font-mono tabular-nums">{formatMinor(minor, currency)}</span>
);

function percent(p: number | null | undefined): string {
	return p == null ? "—" : `${p.toLocaleString("en-IN", { maximumFractionDigits: 1 })}%`;
}

function unpricedText(n: number): string {
	return n === 1 ? s.unpricedOne : fill(s.unpriced, { n: formatNumber(n) });
}

function columns(currency: Currency): Column<PnlRow>[] {
	return [
		{
			id: "customer",
			header: s.columns.customer,
			rowHeader: true,
			sortValue: (r) => r.name,
			cell: (r) => (
				<span className="flex flex-wrap items-center gap-2">
					{r.name}
					{r.exempt ? <Badge variant="default">{s.internal}</Badge> : null}
				</span>
			),
		},
		{
			id: "billed",
			header: s.columns.billed,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.billed_minor,
			cell: (r) => money(r.billed_minor, currency),
		},
		{
			id: "collected",
			header: s.columns.collected,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.collected_minor,
			cell: (r) => money(r.collected_minor, currency),
		},
		{
			id: "adjustments",
			header: s.columns.adjustments,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.adjustments_minor,
			cell: (r) => money(r.adjustments_minor, currency),
		},
		{
			id: "cost",
			header: s.columns.cost,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.ai_cost_minor,
			cell: (r) => (
				<span className="grid justify-items-end gap-0.5">
					{money(r.ai_cost_minor, currency)}
					<small className="flex flex-wrap items-center justify-end gap-1 text-[11.5px] text-muted-foreground">
						<Badge variant="default" data-testid="estimate-badge">
							{s.estimate}
						</Badge>
						{!r.ai_cost_known ? <span>{unpricedText(r.unpriced_calls ?? 0)}</span> : null}
					</small>
				</span>
			),
		},
		{
			id: "margin",
			header: s.columns.margin,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.margin_minor,
			cell: (r) => (
				<span className={cn("font-mono tabular-nums", r.margin_minor < 0 ? "text-destructive" : "text-foreground")}>
					{formatMinor(r.margin_minor, currency)}
				</span>
			),
		},
		{
			id: "percent",
			header: s.columns.percent,
			align: "right",
			stackLabel: true,
			sortValue: (r) => r.margin_pct ?? Number.NEGATIVE_INFINITY,
			cell: (r) => <span className="font-mono tabular-nums">{percent(r.margin_pct)}</span>,
		},
	];
}

function Table({ report }: { report: PnlReport }) {
	const currency = report.currency;
	const t = report.totals;
	const footer = {
		customer: (
			<span className="flex flex-wrap items-center gap-2 font-semibold">
				{s.total}
				{!t.ai_cost_known ? (
					<small className="font-normal text-muted-foreground">{unpricedText(t.unpriced_calls ?? 0)}</small>
				) : null}
			</span>
		),
		billed: <strong className="font-mono tabular-nums">{formatMinor(t.billed_minor, currency)}</strong>,
		collected: <strong className="font-mono tabular-nums">{formatMinor(t.collected_minor, currency)}</strong>,
		adjustments: <strong className="font-mono tabular-nums">{formatMinor(t.adjustments_minor, currency)}</strong>,
		cost: <strong className="font-mono tabular-nums">{formatMinor(t.ai_cost_minor, currency)}</strong>,
		margin: <strong className="font-mono tabular-nums">{formatMinor(t.margin_minor, currency)}</strong>,
		percent: <strong className="font-mono tabular-nums">{percent(t.margin_pct)}</strong>,
	};
	return (
		<div className="overflow-x-auto" data-testid="pnl-table">
			<DataTable
				caption={s.caption}
				columns={columns(currency)}
				rows={report.rows}
				rowKey={(r) => r.tenant_id}
				initialSort={{ id: "margin", dir: "desc" }}
				footer={footer}
			/>
		</div>
	);
}

/**
 * The P&L tab (BIL6, admin): per customer over a month range, what they were billed and paid
 * against what their answers cost the operator's AI key, with the margin, in one chosen currency.
 * AI cost is an estimate and says how many calls had no known price. Exportable as CSV.
 */
export function PnlTab() {
	const fromId = useId();
	const toId = useId();
	const currencyId = useId();
	const thisMonth = monthOf(new Date());
	const [start, setStart] = useState(thisMonth);
	const [end, setEnd] = useState(thisMonth);
	const [currency, setCurrency] = useState<Currency>("INR");
	const months = monthsBetween(start, end);
	const rangeError =
		start === "" || end === "" ? null : months === 0 ? s.rangeOrder : months > MAX_PNL_MONTHS ? s.rangeLong : null;
	const ready = start !== "" && end !== "" && rangeError === null;
	const q = useQuery({ ...pnlQuery({ start, end, currency }), enabled: ready });

	let body: React.ReactNode;
	if (!ready) body = null;
	else if (q.isPending) body = <TableSkeleton rows={4} />;
	else if (q.isError)
		body = (
			<div className="p-4">
				<ErrorState error={q.error} title={s.loadError} onRetry={() => void q.refetch()} />
			</div>
		);
	else if (q.data.rows.length === 0)
		body = (
			<p data-testid="pnl-empty" className="m-0 p-6 text-foreground-2">
				{s.empty}
			</p>
		);
	else body = <Table report={q.data} />;

	return (
		<Panel
			title={s.title}
			sub={s.lead}
			aside={
				<Button
					type="button"
					size="sm"
					variant="ghost"
					disabled={!q.data || q.data.rows.length === 0}
					onClick={() => q.data && downloadText(pnlCsv(q.data), `pnl-${q.data.start}-to-${q.data.end}.csv`)}
				>
					<Download aria-hidden className="size-4" />
					{s.exportCsv}
				</Button>
			}
		>
			<div data-testid="pnl">
				<div className="flex flex-wrap items-end gap-3 border-b border-border p-4">
					<div>
						<label htmlFor={fromId} className="mb-1 block text-[13px] font-semibold">
							{s.from}
						</label>
						<Input id={fromId} type="month" value={start} onChange={(e) => setStart(e.target.value)} />
					</div>
					<div>
						<label htmlFor={toId} className="mb-1 block text-[13px] font-semibold">
							{s.to}
						</label>
						<Input id={toId} type="month" value={end} onChange={(e) => setEnd(e.target.value)} />
					</div>
					<div className="min-w-[120px]">
						<label htmlFor={currencyId} className="mb-1 block text-[13px] font-semibold">
							{s.currency}
						</label>
						<NativeSelect id={currencyId} value={currency} onChange={(e) => setCurrency(e.target.value as Currency)}>
							<option value="INR">₹ INR</option>
							<option value="USD">$ USD</option>
						</NativeSelect>
					</div>
				</div>
				{rangeError ? (
					<p role="alert" className="m-0 border-b border-border px-4 py-2.5 text-[13px] text-destructive">
						{rangeError}
					</p>
				) : null}
				{body}
				{q.data && q.data.rows.length > 0 ? (
					<p
						data-testid="pnl-note"
						className="m-0 border-t border-border px-4 py-3 text-[12.5px] text-muted-foreground"
					>
						{fill(s.note, { rate: String(q.data.rate) })}
					</p>
				) : null}
			</div>
		</Panel>
	);
}
