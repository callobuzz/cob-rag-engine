import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Search } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { useDebouncedValue } from "@/features/documents/ui";
import type { BillingAccountRow } from "@/lib/api/billing-types";
import { formatMinor } from "@/lib/format/money"; // BIL3 ledger
import { billingStrings as s } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";
import { ACCOUNTS_PAGE_SIZE, billingAccountsQuery } from "../api";
import { ReconciliationCard } from "../ledger/ReconciliationCard"; // BIL3 ledger

type PaymentFilter = "" | "prepaid" | "postpaid";

function statusOf(row: BillingAccountRow): { label: string; variant: "ok" | "default" | "warn" | "bad" } {
	// BIL4 state: the engine's own reading of the account (balance, grace, override)
	if (row.exempt || row.status === "not_billed") return { label: s.accounts.status.exempt, variant: "default" };
	if (row.status === "forced") return { label: s.accounts.status.forced, variant: "warn" };
	if (row.status === "paused") return { label: s.accounts.status.paused, variant: "bad" };
	if (row.status === "low") return { label: s.accounts.status.low, variant: "warn" };
	if (row.pause_override === "never") return { label: s.accounts.status.never, variant: "default" };
	return { label: s.accounts.status.active, variant: "ok" };
}

const columns: Column<BillingAccountRow>[] = [
	{
		id: "customer",
		header: s.accounts.columns.customer,
		rowHeader: true,
		sortValue: (r) => r.tenant_name,
		cell: (r) => (
			<Link
				to="/admin/customers/$tenantId"
				params={{ tenantId: r.tenant_id }}
				search={{ tab: "billing" }}
				className="font-semibold text-foreground no-underline hover:underline"
			>
				{r.tenant_name}
			</Link>
		),
	},
	{ id: "plan", header: s.accounts.columns.plan, stackLabel: true, cell: (r) => r.plan.name },
	{
		id: "payment",
		header: s.accounts.columns.payment,
		stackLabel: true,
		cell: (r) => (r.exempt ? "—" : r.payment_type === "prepaid" ? s.accounts.prepaid : s.accounts.postpaid),
	},
	{ id: "currency", header: s.accounts.columns.currency, stackLabel: true, cell: (r) => r.currency },
	{
		id: "balance",
		header: s.accounts.columns.balance,
		stackLabel: true,
		align: "right",
		// BIL3 ledger
		cell: (r) => (
			<span className="font-mono tabular-nums" data-testid="account-balance">
				{r.exempt ? s.accounts.balancePlaceholder : formatMinor(r.balance_minor, r.currency)}
			</span>
		),
		// /BIL3 ledger
	},
	{
		id: "status",
		header: s.accounts.columns.status,
		stackLabel: true,
		cell: (r) => {
			const st = statusOf(r);
			return <Badge variant={st.variant}>{st.label}</Badge>;
		},
	},
	{
		id: "terms",
		header: s.accounts.columns.terms,
		stackLabel: true,
		cell: (r) =>
			r.overrides_count > 0 ? (
				<Badge variant="info">{fill(s.accounts.termsCustom, { count: String(r.overrides_count) })}</Badge>
			) : (
				<span className="text-muted-foreground">{s.accounts.termsPlan}</span>
			),
	},
];

/** Billing → Accounts: every customer, how they pay, and a link to their terms. */
export function AccountsTab() {
	const searchId = useId();
	const filterId = useId();
	const [text, setText] = useState("");
	const [payment, setPayment] = useState<PaymentFilter>("");
	const [page, setPage] = useState(1);
	const q = useDebouncedValue(text, 300).trim();
	const list = useQuery(
		billingAccountsQuery({
			q: q || undefined,
			payment_type: payment || undefined,
			offset: (page - 1) * ACCOUNTS_PAGE_SIZE,
		}),
	);
	const rows = list.data?.items ?? [];
	const total = list.data?.total ?? 0;
	const filtered = q !== "" || payment !== "";

	let body: ReactNode;
	if (list.isPending) body = <TableSkeleton rows={4} />;
	else if (list.isError)
		body = (
			<div className="p-4">
				<ErrorState error={list.error} onRetry={() => void list.refetch()} />
			</div>
		);
	else if (rows.length === 0)
		body = (
			<p data-testid="accounts-empty" className="m-0 p-6 text-foreground-2">
				{filtered ? s.accounts.empty : s.accounts.emptyAll}
			</p>
		);
	else
		body = (
			<>
				<DataTable
					caption={s.accounts.caption}
					columns={columns}
					rows={rows}
					rowKey={(r) => r.tenant_id}
					initialSort={{ id: "customer", dir: "asc" }}
				/>
				{total > ACCOUNTS_PAGE_SIZE ? (
					<Pager page={page} pageSize={ACCOUNTS_PAGE_SIZE} total={total} onPage={setPage} />
				) : null}
			</>
		);

	return (
		<div className="grid gap-4" data-testid="billing-accounts">
			<ReconciliationCard /> {/* BIL3 ledger */}
			<div className="flex flex-wrap items-end gap-3">
				<div className="relative min-w-[220px] max-w-[420px] flex-1">
					<label htmlFor={searchId} className="sr-only">
						{s.accounts.search}
					</label>
					<Search
						className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
						aria-hidden
					/>
					<Input
						id={searchId}
						type="search"
						className="pl-9"
						placeholder={s.accounts.searchPlaceholder}
						value={text}
						maxLength={200}
						onChange={(e) => {
							setText(e.target.value);
							setPage(1);
						}}
					/>
				</div>
				<div className="min-w-[200px]">
					<label htmlFor={filterId} className="sr-only">
						{s.accounts.paymentFilter}
					</label>
					<NativeSelect
						id={filterId}
						value={payment}
						onChange={(e) => {
							setPayment(e.target.value as PaymentFilter);
							setPage(1);
						}}
					>
						<option value="">{s.accounts.allPayments}</option>
						<option value="prepaid">{s.accounts.prepaid}</option>
						<option value="postpaid">{s.accounts.postpaid}</option>
					</NativeSelect>
				</div>
			</div>
			<div className="overflow-x-auto rounded-lg border border-border bg-card">{body}</div>
		</div>
	);
}
