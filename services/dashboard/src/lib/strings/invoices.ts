/**
 * Tax invoices (P2): what a business owner reads in Plan & billing and what the provider reads in
 * Billing → Invoices. Owners read "tax invoice", "credit note", "GST", "download"; never "ledger",
 * "document" or "tenant". Examples are Indian: Sharma Furniture, Pune, GST.
 */
export const invoiceStrings = {
	/** The owner's card on Plan & billing. */
	card: {
		title: "Tax invoices",
		lead: "A tax invoice for every payment, ready for your GST records.",
		empty: "Your tax invoices will appear here after your first payment.",
		download: "Download",
		downloadLabel: "Download {number}",
		preparing: "Preparing…",
		stillPreparing: "Your invoice is still being prepared. Please try again in a few seconds.",
		downloadFailed: "We couldn't download that invoice. Please try again.",
		loadError: "We couldn't load your tax invoices.",
		type: { invoice: "Tax invoice", bill_of_supply: "Bill of supply", credit_note: "Credit note" },
		creditFor: "Against {number}",
		sentTo: "Your invoices are sent to {email}.",
		sentToOwners: "Your invoices are sent to the owners of this workspace.",
		changeDetails: "Change tax details",
		columns: { number: "Number", date: "Date", amount: "Amount" },
	},
	/** Billing → Invoices (the provider). */
	admin: {
		tab: "Invoices",
		title: "Tax invoices",
		lead: "Every tax invoice and credit note issued. Download the month's CSV for your accountant.",
		filters: {
			workspace: "Workspace",
			allWorkspaces: "All workspaces",
			type: "Type",
			allTypes: "All types",
			year: "Financial year",
			allYears: "All years",
			from: "From",
			to: "To",
		},
		totals: {
			label: "Totals for these documents",
			taxable: "Taxable value",
			cgst: "CGST",
			sgst: "SGST",
			igst: "IGST",
			total: "Total",
		},
		columns: {
			number: "Number",
			date: "Date",
			workspace: "Workspace",
			type: "Type",
			taxable: "Taxable",
			tax: "GST",
			total: "Total",
		},
		empty: "No tax documents yet.",
		emptyFiltered: "No tax documents match these filters.",
		loadError: "We couldn't load the tax documents.",
		download: "Download",
		showOnly: "Show only this workspace",
		onlyThis: "Show only the documents of {name} (from {number})",
		resend: "Resend",
		resending: "Sending…",
		resent: "Sent again",
		resentBody: "Invoice {number} was e-mailed again to the business's invoice address.",
		pdfPending: "The PDF is still being prepared. Try again in a few seconds.",
		csv: {
			title: "For your accountant",
			lead: "One row per document in the month, with the GST split, ready for your returns.",
			month: "Month",
			button: "Download CSV for your accountant",
			downloading: "Preparing…",
			failed: "We couldn't prepare the CSV. Please try again.",
			fileName: "tax-documents-{from}-{to}.csv",
		},
		irn: "E-invoicing (IRN) is needed only above ₹5 crore turnover; this engine does not do it.",
	},
	/** A price with its GST-inclusive total next to it. */
	price: {
		inclusive: "{total} incl. GST",
		plusGst: "{price} + GST",
		inclusiveMonthly: "{total} a month incl. GST",
		hint: "With GST: {total}",
		feeWithGst: "+ GST · {total} incl. GST",
		feeIncludesGst: "(GST included)",
	},
	/** The refund dialog's warning when no credit note can be issued any more. */
	refund: {
		noCreditNote:
			"A credit note can no longer be issued for this payment (deadline {date}). The GST already paid stays paid.",
		creditNoteNote: "A credit note for this refund is issued automatically.",
	},
};
