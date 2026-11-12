import { useQuery } from "@tanstack/react-query";
import { CreditCard, Pencil } from "lucide-react";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import type { OwnBillingView } from "@/lib/api/billing-types";
import { inclusiveTotal } from "@/lib/format/gst"; // P2 invoices
import { formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { invoiceStrings } from "@/lib/strings/invoices"; // P2 invoices
import { paymentStrings } from "@/lib/strings/payments";
import { AddMoneyDialog } from "./AddMoneyDialog";
import { paymentConfigQuery, taxDetailsQuery } from "./api";
import { goToPay } from "./checkout";
import { TaxDetailsDialog } from "./TaxDetailsDialog";

const s = paymentStrings.addMoney;
const t = paymentStrings.taxDetails;

/**
 * Plan & billing: "Add money", "Pay plan fee" and the tax details the invoices carry (P1). Shown
 * only where online payment is switched on. Members see what is there without the buttons.
 */
export function PayCard({ view, canPay }: { view: OwnBillingView; canPay: boolean }) {
	const config = useQuery({ ...paymentConfigQuery, retry: false });
	const details = useQuery({ ...taxDetailsQuery, enabled: config.data?.enabled === true && canPay });
	const [adding, setAdding] = useState(false);
	const [editing, setEditing] = useState(false);
	const [resume, setResume] = useState(false);
	const [carry, setCarry] = useState<number | null>(null);
	if (!config.data?.enabled) return null;

	const fee = view.subscription_fee_minor ?? 0;
	const owed = (view.available_minor ?? 0) < 0 ? -(view.available_minor ?? 0) : undefined;
	const d = details.data;
	const abroad = Boolean(d?.country) && d?.country !== "IN"; // P2 invoices: an export pays no GST
	return (
		<section
			aria-label={paymentStrings.payments.title}
			data-testid="pay-card"
			className="grid gap-3 rounded-lg border border-border bg-card px-4 py-4"
		>
			{config.data.mode === "test" ? (
				<p className="m-0 text-[12px] font-bold tracking-wide text-warning">{paymentStrings.pay.testMode}</p>
			) : null}
			{canPay ? (
				<div className="flex flex-wrap gap-2">
					<Button type="button" variant="default" onClick={() => setAdding(true)}>
						<CreditCard aria-hidden className="size-4" />
						{s.button}
					</Button>
					{fee > 0 ? (
						<Button
							type="button"
							variant="outline"
							onClick={() => {
								if (config.data?.tax_details_complete) goToPay({ purpose: "plan" });
								else {
									setResume(false);
									setEditing(true);
								}
							}}
						>
							{s.payPlan}
						</Button>
					) : null}
				</div>
			) : (
				<p className="m-0 text-[13px] text-muted-foreground">{s.ownersOnly}</p>
			)}
			{fee > 0 ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">
					{fill(s.planFeeNote, { fee: formatMinor(fee, view.currency) })}
					{/* P2 invoices: the GST-inclusive total next to the fee (no drip pricing) */}
					{view.gst?.registered && view.currency === "INR" && !abroad ? (
						<span data-testid="plan-fee-gst">
							{" "}
							{view.gst.prices_include_gst
								? invoiceStrings.price.feeIncludesGst
								: fill(invoiceStrings.price.feeWithGst, {
										total: formatMinor(inclusiveTotal(fee, true, false), "INR"),
									})}
						</span>
					) : null}
					{/* /P2 invoices */}
				</p>
			) : null}
			{canPay ? (
				<div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-3">
					<div id="tax-details" className="min-w-0 text-[13px]">
						<div className="font-semibold">{t.title}</div>
						<div data-testid="tax-details-summary" className="text-muted-foreground">
							{d?.complete ? fill(t.summary, { name: d.legal_name, city: d.city }) : t.notSet}
						</div>
					</div>
					<Button type="button" variant="ghost" size="sm" onClick={() => setEditing(true)}>
						<Pencil aria-hidden className="size-4" />
						{t.title}
					</Button>
				</div>
			) : null}
			<AddMoneyDialog
				open={adding}
				onOpenChange={setAdding}
				suggested={carry ?? (owed ? Math.max(owed, config.data.min_top_up_minor) : undefined)}
				onNeedDetails={(amount) => {
					setCarry(amount);
					setResume(true);
					setEditing(true);
				}}
			/>
			<TaxDetailsDialog
				open={editing}
				onOpenChange={setEditing}
				onSaved={() => {
					if (resume) setAdding(true);
					setResume(false);
				}}
			/>
		</section>
	);
}
