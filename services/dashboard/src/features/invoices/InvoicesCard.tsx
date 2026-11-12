import { useQuery } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { paymentConfigQuery, taxDetailsQuery } from "@/features/payments/api";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date";
import { formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { invoiceStrings } from "@/lib/strings/invoices";
import { loadOwnInvoicePdf, OWN_PAGE, ownInvoicesQuery, type TaxDocument } from "./api";
import { pdfFileName, saveBlob } from "./download";

const s = invoiceStrings.card;

/**
 * Plan & billing → Tax invoices (P2): every tax invoice and credit note of this workspace with a
 * Download button. Owners only. Invoices must stay downloadable for years, so the card follows the
 * documents, not the payment switch: it shows whenever there is one, and (to keep the empty-state
 * text) whenever online payment is on. A brand-new invoice says "Preparing…" and refreshes itself.
 */
export function InvoicesCard() {
	const config = useQuery({ ...paymentConfigQuery, retry: false });
	const enabled = config.data?.enabled === true;
	const [page, setPage] = useState(1);
	const q = useQuery({ ...ownInvoicesQuery((page - 1) * OWN_PAGE), retry: false, placeholderData: (prev) => prev });
	const details = useQuery({ ...taxDetailsQuery, enabled });
	const failure = q.isError ? toApiError(q.error) : null;
	const billingOff = failure !== null && isApiError(failure) && failure.code === "billing_disabled";
	if (billingOff || (!enabled && !(q.data?.total ?? 0))) return null;
	const rows = q.data?.items ?? [];
	const email = details.data?.email_for_invoices;
	return (
		<section
			aria-label={s.title}
			data-testid="invoices-card"
			className="min-w-0 rounded-lg border border-border bg-card"
		>
			<div className="border-b border-border px-4 py-3.5">
				<h2 className="text-[15px] font-bold">{s.title}</h2>
				<div className="text-[12.5px] text-muted-foreground">{s.lead}</div>
			</div>
			{q.isPending ? (
				<TableSkeleton rows={2} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={s.loadError} onRetry={() => void q.refetch()} />
				</div>
			) : rows.length === 0 ? (
				<p data-testid="invoices-empty" className="m-0 px-4 py-4 text-[13px] text-muted-foreground">
					{s.empty}
				</p>
			) : (
				<>
					<ul className="m-0 list-none p-0">
						{rows.map((d) => (
							<InvoiceRow key={d.id} doc={d} />
						))}
					</ul>
					{(q.data?.total ?? 0) > OWN_PAGE ? (
						<Pager page={page} pageSize={OWN_PAGE} total={q.data?.total ?? 0} onPage={setPage} />
					) : null}
				</>
			)}
			<p
				data-testid="invoices-sent-to"
				className="m-0 border-t border-border px-4 py-3 text-[12.5px] text-muted-foreground"
			>
				{email ? fill(s.sentTo, { email }) : s.sentToOwners}
				{enabled ? (
					<>
						{" "}
						<a href="#tax-details" className="font-semibold text-primary underline-offset-2 hover:underline">
							{s.changeDetails}
						</a>
					</>
				) : null}
			</p>
		</section>
	);
}

function InvoiceRow({ doc }: { doc: TaxDocument }) {
	const [busy, setBusy] = useState(false);
	const download = async () => {
		setBusy(true);
		try {
			saveBlob(await loadOwnInvoicePdf(doc.id), pdfFileName(doc.number));
		} catch (e) {
			const err = toApiError(e);
			toast.error(
				isApiError(err) && err.code === "tax_document_pdf_pending" ? s.stillPreparing : messageFor(err).title,
			);
		} finally {
			setBusy(false);
		}
	};
	return (
		<li
			data-testid="invoice-row"
			className="grid gap-0.5 border-b border-border px-4 py-3 text-[13.5px] last:border-b-0"
		>
			<div className="flex flex-wrap items-baseline justify-between gap-2">
				<span className="font-mono font-semibold">{doc.number}</span>
				<span className="font-mono tabular-nums">{formatMinor(doc.total_minor, doc.currency as "INR")}</span>
			</div>
			<div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-muted-foreground">
				<span className="flex flex-wrap items-center gap-2">
					{formatDate(doc.issued_on)}
					<Badge variant={doc.doc_type === "credit_note" ? "default" : "ok"}>{s.type[doc.doc_type]}</Badge>
					{doc.original_number ? <span>{fill(s.creditFor, { number: doc.original_number })}</span> : null}
				</span>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					disabled={busy || !doc.pdf_ready}
					aria-label={fill(s.downloadLabel, { number: doc.number })}
					onClick={() => void download()}
				>
					<Download aria-hidden className="size-4" />
					{doc.pdf_ready ? s.download : s.preparing}
				</Button>
			</div>
		</li>
	);
}
