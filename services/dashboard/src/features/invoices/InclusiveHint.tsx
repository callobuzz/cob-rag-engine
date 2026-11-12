import { useQuery } from "@tanstack/react-query";
import { taxProfileQuery } from "@/features/payments/api";
import { inclusiveTotal } from "@/lib/format/gst";
import { formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { invoiceStrings } from "@/lib/strings/invoices";

const s = invoiceStrings.price;

/**
 * Under a monthly fee typed in the pricing dialog (P2): what a business pays with GST, so the
 * provider sees the same figure the business will. Shown only for rupees and a GST-registered seller.
 */
export function InclusiveHint({ feeMinor, currency }: { feeMinor: number; currency: string }) {
	const profile = useQuery({ ...taxProfileQuery, retry: false });
	if (currency !== "INR" || feeMinor <= 0 || !profile.data?.gstin) return null;
	const total = inclusiveTotal(feeMinor, true, profile.data.prices_include_gst);
	return (
		<small data-testid="pricing-inclusive-hint" className="text-[12.5px] text-muted-foreground">
			{fill(s.inclusiveMonthly, { total: formatMinor(total, "INR") })}
		</small>
	);
}
