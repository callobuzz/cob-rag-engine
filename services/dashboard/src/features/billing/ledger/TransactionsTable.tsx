import { useQuery } from "@tanstack/react-query";
import { Download, Plus } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { downloadText } from "@/features/usage/csv";
import type { LedgerEntry, LedgerEntryKind } from "@/lib/api/billing-types";
import { formatDateTime } from "@/lib/format/date";
import { type Currency, formatMinor } from "@/lib/format/money";
import { ledgerStrings as s } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { Panel } from "../../kbs/ui";
import { AddTransactionDialog } from "./AddTransactionDialog";
import { LEDGER_PAGE_SIZE, ledgerQuery } from "./api";
import { ledgerCsv } from "./csv";

const TYPES = Object.keys(s.types) as LedgerEntryKind[];

function amountCell(e: LedgerEntry): ReactNode {
	const text = formatMinor(e.amount_minor, e.currency);
	return (
		<span
			className={cn("font-mono tabular-nums", e.amount_minor > 0 ? "text-success" : "text-foreground")}
			data-testid="ledger-amount"
		>
			{e.amount_minor > 0 ? `+${text}` : text}
		</span>
	);
}

const columns: Column<LedgerEntry>[] = [
	{
		id: "date",
		header: s.columns.date,
		rowHeader: true,
		stackLabel: true,
		cell: (e) => <span className="whitespace-nowrap">{formatDateTime(e.created_at)}</span>,
	},
	{ id: "type", header: s.columns.type, stackLabel: true, cell: (e) => s.types[e.entry_type] },
	{
		id: "description",
		header: s.columns.description,
		cell: (e) => (
			<div className="grid gap-0.5">
				<span>{e.memo_public ?? "—"}</span>
				{e.memo ? (
					<small className="text-[12px] text-muted-foreground">{fill(s.adminNote, { note: e.memo })}</small>
				) : null}
				{e.actor_label && e.actor_label !== "system" ? (
					<small className="text-[12px] text-muted-foreground">{fill(s.by, { who: e.actor_label })}</small>
				) : null}
			</div>
		),
	},
	{ id: "amount", header: s.columns.amount, stackLabel: true, align: "right", cell: amountCell },
	{
		id: "balance",
		header: s.columns.balanceAfter,
		stackLabel: true,
		align: "right",
		cell: (e) => <span className="font-mono tabular-nums">{formatMinor(e.balance_after_minor, e.currency)}</span>,
	},
];

/**
 * Transactions (BIL3): one customer's wallet entries, newest first, with their balance and what
 * is still available, filters by type and date, a CSV of the page, and "Add a transaction".
 */
export function TransactionsTable({
	tenantId,
	tenantName,
	currency,
}: {
	tenantId: string;
	tenantName: string;
	currency: Currency;
}) {
	const typeId = useId();
	const fromId = useId();
	const toId = useId();
	const [type, setType] = useState<"" | LedgerEntryKind>("");
	const [from, setFrom] = useState("");
	const [to, setTo] = useState("");
	const [page, setPage] = useState(1);
	const [adding, setAdding] = useState(false);
	const list = useQuery(
		ledgerQuery(tenantId, {
			entry_type: type || undefined,
			start: from || undefined,
			end: to || undefined,
			offset: (page - 1) * LEDGER_PAGE_SIZE,
		}),
	);
	const items = list.data?.items ?? [];
	const total = list.data?.total ?? 0;
	const filtered = type !== "" || from !== "" || to !== "";

	let body: ReactNode;
	if (list.isPending) body = <TableSkeleton rows={4} />;
	else if (list.isError)
		body = (
			<div className="p-4">
				<ErrorState error={list.error} title={s.loadError} onRetry={() => void list.refetch()} />
			</div>
		);
	else if (items.length === 0)
		body = (
			<p data-testid="ledger-empty" className="m-0 p-6 text-foreground-2">
				{filtered ? s.emptyFiltered : s.empty}
			</p>
		);
	else
		body = (
			<>
				<div className="overflow-x-auto">
					<DataTable caption={s.caption} columns={columns} rows={items} rowKey={(e) => e.id} />
				</div>
				{total > LEDGER_PAGE_SIZE ? (
					<Pager page={page} pageSize={LEDGER_PAGE_SIZE} total={total} onPage={setPage} />
				) : null}
			</>
		);

	return (
		<Panel
			title={s.title}
			sub={s.lead}
			aside={
				<div className="flex flex-wrap gap-2">
					<Button
						type="button"
						size="sm"
						variant="ghost"
						disabled={items.length === 0}
						onClick={() => downloadText(ledgerCsv(items), `transactions-${tenantName}.csv`)}
					>
						<Download aria-hidden className="size-4" />
						{s.exportCsv}
					</Button>
					<Button type="button" size="sm" variant="default" onClick={() => setAdding(true)}>
						<Plus aria-hidden className="size-4" />
						{s.add}
					</Button>
				</div>
			}
		>
			<div data-testid="customer-ledger">
				{list.data ? (
					<dl className="m-0 grid gap-4 border-b border-border p-4 min-[600px]:grid-cols-2">
						<div>
							<dt className="text-[12.5px] text-muted-foreground">{s.balance}</dt>
							<dd className="m-0 font-mono text-[20px] font-bold tabular-nums" data-testid="ledger-balance">
								{formatMinor(list.data.balance_minor, currency)}
							</dd>
						</div>
						<div>
							<dt className="text-[12.5px] text-muted-foreground">{s.available}</dt>
							<dd className="m-0 grid">
								<span className="font-mono text-[20px] font-bold tabular-nums" data-testid="ledger-available">
									{formatMinor(list.data.available_minor, currency)}
								</span>
								<small className="text-[12px] text-muted-foreground">{s.availableNote}</small>
							</dd>
						</div>
					</dl>
				) : null}
				<div className="flex flex-wrap items-end gap-3 border-b border-border p-4">
					<div className="min-w-[180px]">
						<label htmlFor={typeId} className="mb-1 block text-[13px] font-semibold">
							{s.filters.type}
						</label>
						<NativeSelect
							id={typeId}
							value={type}
							onChange={(e) => {
								setType(e.target.value as "" | LedgerEntryKind);
								setPage(1);
							}}
						>
							<option value="">{s.filters.allTypes}</option>
							{TYPES.map((k) => (
								<option key={k} value={k}>
									{s.types[k]}
								</option>
							))}
						</NativeSelect>
					</div>
					<div>
						<label htmlFor={fromId} className="mb-1 block text-[13px] font-semibold">
							{s.filters.from}
						</label>
						<Input
							id={fromId}
							type="date"
							value={from}
							onChange={(e) => {
								setFrom(e.target.value);
								setPage(1);
							}}
						/>
					</div>
					<div>
						<label htmlFor={toId} className="mb-1 block text-[13px] font-semibold">
							{s.filters.to}
						</label>
						<Input
							id={toId}
							type="date"
							value={to}
							onChange={(e) => {
								setTo(e.target.value);
								setPage(1);
							}}
						/>
					</div>
				</div>
				{body}
			</div>
			<AddTransactionDialog
				open={adding}
				onOpenChange={setAdding}
				tenantId={tenantId}
				tenantName={tenantName}
				currency={currency}
			/>
		</Panel>
	);
}
