import { useQuery } from "@tanstack/react-query";
import { Download, Mail } from "lucide-react";
import { type ReactNode, useId, useState } from "react";
import { toast } from "sonner";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { BOM, downloadText } from "@/features/usage/csv";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date";
import { formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { invoiceStrings } from "@/lib/strings/invoices";
import {
	type AdminFilters,
	type AdminTaxDocument,
	adminInvoicesQuery,
	type DocType,
	INVOICES_PAGE,
	invoiceTenantsQuery,
	loadAdminInvoicePdf,
	loadInvoiceCsv,
	useResendInvoice,
} from "../api";
import { monthRange, pdfFileName, saveBlob } from "../download";

const s = invoiceStrings.admin;
const money = (n: number) => formatMinor(n, "INR");
const TYPES: DocType[] = ["invoice", "bill_of_supply", "credit_note"];

/** The financial years from the first one the engine could have issued in until this one. */
function financialYears(now = new Date()): string[] {
	const start = now.getMonth() >= 3 ? now.getFullYear() : now.getFullYear() - 1;
	const out: string[] = [];
	for (let y = start; y >= start - 2; y--) {
		out.push(`${String(y % 100).padStart(2, "0")}-${String((y + 1) % 100).padStart(2, "0")}`);
	}
	return out;
}

function currentMonth(now = new Date()): string {
	return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
}

/** Billing → Invoices (P2): every tax document, with totals, a PDF per row and the accountant's CSV. */
export function InvoicesTab() {
	return (
		<div className="grid gap-6" data-testid="billing-invoices">
			<InvoiceTable />
			<AccountantCsv />
			<p data-testid="irn-note" className="m-0 text-[12.5px] text-muted-foreground">
				{s.irn}
			</p>
		</div>
	);
}

function InvoiceTable() {
	const id = useId();
	const [tenant, setTenant] = useState("");
	const [docType, setDocType] = useState<DocType | "">("");
	const [fy, setFy] = useState("");
	const [start, setStart] = useState("");
	const [end, setEnd] = useState("");
	const [page, setPage] = useState(1);
	const filters: AdminFilters = {
		tenant: tenant || undefined,
		docType: docType || undefined,
		fy: fy || undefined,
		start: start || undefined,
		end: end || undefined,
		offset: (page - 1) * INVOICES_PAGE,
	};
	const q = useQuery(adminInvoicesQuery(filters));
	const tenants = useQuery(invoiceTenantsQuery);
	const rows = q.data?.items ?? [];
	const totals = q.data?.totals;
	// a workspace picked from a row may be beyond the list that was loaded: keep it selectable
	const known = tenants.data?.items ?? [];
	const picked = tenant && !known.some((t) => t.id === tenant) ? rows.find((r) => r.tenant_id === tenant) : undefined;
	const filtered = Boolean(tenant || docType || fy || start || end);
	const reset = (set: () => void) => {
		set();
		setPage(1);
	};

	const columns: Column<AdminTaxDocument>[] = [
		{
			id: "number",
			header: s.columns.number,
			rowHeader: true,
			cell: (r) => <span className="font-mono">{r.number}</span>,
		},
		{ id: "date", header: s.columns.date, stackLabel: true, cell: (r) => formatDate(r.issued_on) },
		{
			id: "workspace",
			header: s.columns.workspace,
			stackLabel: true,
			cell: (r) => (
				<span className="grid justify-items-start gap-0.5">
					{r.buyer_name}
					{tenant !== r.tenant_id ? (
						<button
							type="button"
							className="cursor-pointer border-0 bg-transparent p-0 text-[12px] font-semibold text-primary underline-offset-2 hover:underline"
							aria-label={fill(s.onlyThis, { name: r.buyer_name, number: r.number })}
							onClick={() => reset(() => setTenant(r.tenant_id))}
						>
							{s.showOnly}
						</button>
					) : null}
				</span>
			),
		},
		{
			id: "type",
			header: s.columns.type,
			stackLabel: true,
			cell: (r) => (
				<Badge variant={r.doc_type === "credit_note" ? "default" : "ok"}>{invoiceStrings.card.type[r.doc_type]}</Badge>
			),
		},
		{
			id: "taxable",
			header: s.columns.taxable,
			stackLabel: true,
			align: "right",
			cell: (r) => <span className="font-mono tabular-nums">{money(r.taxable_minor)}</span>,
		},
		{
			id: "tax",
			header: s.columns.tax,
			stackLabel: true,
			align: "right",
			cell: (r) => <span className="font-mono tabular-nums">{money(r.cgst_minor + r.sgst_minor + r.igst_minor)}</span>,
		},
		{
			id: "total",
			header: s.columns.total,
			stackLabel: true,
			align: "right",
			cell: (r) => <span className="font-mono tabular-nums">{money(r.total_minor)}</span>,
		},
		{ id: "actions", header: "", cell: (r) => <RowActions doc={r} /> },
	];

	let body: ReactNode;
	if (q.isPending) body = <TableSkeleton rows={4} />;
	else if (q.isError)
		body = (
			<div className="p-4">
				<ErrorState error={q.error} title={s.loadError} onRetry={() => void q.refetch()} />
			</div>
		);
	else if (rows.length === 0)
		body = (
			<p data-testid="invoices-table-empty" className="m-0 p-6 text-foreground-2">
				{filtered ? s.emptyFiltered : s.empty}
			</p>
		);
	else
		body = (
			<>
				<DataTable caption={s.title} columns={columns} rows={rows} rowKey={(r) => r.id} />
				{(q.data?.total ?? 0) > INVOICES_PAGE ? (
					<Pager page={page} pageSize={INVOICES_PAGE} total={q.data?.total ?? 0} onPage={setPage} />
				) : null}
			</>
		);

	return (
		<section
			aria-label={s.title}
			className="min-w-0 rounded-lg border border-border bg-card"
			data-testid="invoices-table"
		>
			<div className="border-b border-border px-4 py-3.5">
				<h2 className="text-[15px] font-bold">{s.title}</h2>
				<div className="text-[12.5px] text-muted-foreground">{s.lead}</div>
			</div>
			<div className="grid gap-3 border-b border-border px-4 py-3 min-[700px]:grid-cols-3 min-[1000px]:grid-cols-5">
				<FilterBox id={`${id}-ws`} label={s.filters.workspace}>
					<NativeSelect id={`${id}-ws`} value={tenant} onChange={(e) => reset(() => setTenant(e.target.value))}>
						<option value="">{s.filters.allWorkspaces}</option>
						{known.map((t) => (
							<option key={t.id} value={t.id}>
								{t.name}
							</option>
						))}
						{picked ? <option value={picked.tenant_id}>{picked.buyer_name}</option> : null}
					</NativeSelect>
				</FilterBox>
				<FilterBox id={`${id}-type`} label={s.filters.type}>
					<NativeSelect
						id={`${id}-type`}
						value={docType}
						onChange={(e) => reset(() => setDocType(e.target.value as DocType | ""))}
					>
						<option value="">{s.filters.allTypes}</option>
						{TYPES.map((t) => (
							<option key={t} value={t}>
								{invoiceStrings.card.type[t]}
							</option>
						))}
					</NativeSelect>
				</FilterBox>
				<FilterBox id={`${id}-fy`} label={s.filters.year}>
					<NativeSelect id={`${id}-fy`} value={fy} onChange={(e) => reset(() => setFy(e.target.value))}>
						<option value="">{s.filters.allYears}</option>
						{financialYears().map((y) => (
							<option key={y} value={y}>
								{`20${y}`}
							</option>
						))}
					</NativeSelect>
				</FilterBox>
				<FilterBox id={`${id}-from`} label={s.filters.from}>
					<Input id={`${id}-from`} type="date" value={start} onChange={(e) => reset(() => setStart(e.target.value))} />
				</FilterBox>
				<FilterBox id={`${id}-to`} label={s.filters.to}>
					<Input id={`${id}-to`} type="date" value={end} onChange={(e) => reset(() => setEnd(e.target.value))} />
				</FilterBox>
			</div>
			{totals ? (
				<dl
					aria-label={s.totals.label}
					data-testid="invoices-totals"
					className="m-0 grid grid-cols-2 gap-x-4 gap-y-2 border-b border-border bg-secondary px-4 py-3 text-[13px] min-[700px]:grid-cols-5"
				>
					{(
						[
							[s.totals.taxable, totals.taxable_minor],
							[s.totals.cgst, totals.cgst_minor],
							[s.totals.sgst, totals.sgst_minor],
							[s.totals.igst, totals.igst_minor],
							[s.totals.total, totals.total_minor],
						] as const
					).map(([label, value]) => (
						<div key={label}>
							<dt className="text-muted-foreground">{label}</dt>
							<dd className="m-0 font-mono font-semibold tabular-nums">{money(value)}</dd>
						</div>
					))}
				</dl>
			) : null}
			{body}
		</section>
	);
}

function FilterBox({ id, label, children }: { id: string; label: string; children: ReactNode }) {
	return (
		<div className="grid gap-1 text-[12.5px]">
			<label htmlFor={id} className="font-semibold text-muted-foreground">
				{label}
			</label>
			{children}
		</div>
	);
}

function RowActions({ doc }: { doc: AdminTaxDocument }) {
	const [busy, setBusy] = useState(false);
	const resend = useResendInvoice();
	const download = async () => {
		setBusy(true);
		try {
			saveBlob(await loadAdminInvoicePdf(doc.id), pdfFileName(doc.number));
		} catch (e) {
			const err = toApiError(e);
			toast.error(isApiError(err) && err.code === "tax_document_pdf_pending" ? s.pdfPending : messageFor(err).title);
		} finally {
			setBusy(false);
		}
	};
	const send = async () => {
		try {
			await resend.mutateAsync(doc.id);
			toast.success(s.resent, { description: fill(s.resentBody, { number: doc.number }) });
		} catch (e) {
			toast.error(messageFor(toApiError(e)).title);
		}
	};
	return (
		<span className="flex flex-wrap justify-end gap-1">
			<Button
				type="button"
				variant="ghost"
				size="sm"
				disabled={busy || !doc.pdf_ready}
				aria-label={`${s.download} ${doc.number}`}
				onClick={() => void download()}
			>
				<Download aria-hidden className="size-4" />
				{s.download}
			</Button>
			<Button
				type="button"
				variant="ghost"
				size="sm"
				disabled={resend.isPending}
				aria-label={`${s.resend} ${doc.number}`}
				onClick={() => void send()}
			>
				<Mail aria-hidden className="size-4" />
				{resend.isPending ? s.resending : s.resend}
			</Button>
		</span>
	);
}

function AccountantCsv() {
	const id = useId();
	const [month, setMonth] = useState(currentMonth());
	const [busy, setBusy] = useState(false);
	const download = async () => {
		const { start, end } = monthRange(month);
		setBusy(true);
		try {
			const csv = await loadInvoiceCsv(start, end);
			downloadText(`${BOM}${csv}`, fill(s.csv.fileName, { from: start, to: end }));
		} catch (e) {
			toast.error(messageFor(toApiError(e)).title);
		} finally {
			setBusy(false);
		}
	};
	return (
		<section
			aria-label={s.csv.title}
			data-testid="invoices-csv"
			className="grid gap-3 rounded-lg border border-border bg-card px-4 py-4"
		>
			<div>
				<h2 className="text-[15px] font-bold">{s.csv.title}</h2>
				<div className="text-[12.5px] text-muted-foreground">{s.csv.lead}</div>
			</div>
			<div className="flex flex-wrap items-end gap-3">
				<FilterBox id={`${id}-month`} label={s.csv.month}>
					<Input
						id={`${id}-month`}
						type="month"
						value={month}
						max={currentMonth()}
						onChange={(e) => e.target.value && setMonth(e.target.value)}
					/>
				</FilterBox>
				<Button type="button" variant="default" disabled={busy} onClick={() => void download()}>
					<Download aria-hidden className="size-4" />
					{busy ? s.csv.downloading : s.csv.button}
				</Button>
			</div>
		</section>
	);
}
