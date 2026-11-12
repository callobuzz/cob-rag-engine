import { useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { formatDate } from "@/lib/format/date";
import { formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { paymentStrings } from "@/lib/strings/payments";
import { ownPaymentsQuery, paymentConfigQuery } from "./api";

const s = paymentStrings.payments;

/** The online payments this workspace made: date, what for, amount (GST noted), status (P1). */
export function PaymentsCard() {
	const config = useQuery({ ...paymentConfigQuery, retry: false });
	const q = useQuery({ ...ownPaymentsQuery(), enabled: config.data?.enabled === true });
	if (!config.data?.enabled) return null;
	const rows = q.data?.items ?? [];
	return (
		<section
			aria-label={s.title}
			data-testid="payments-card"
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
				<p data-testid="payments-empty" className="m-0 px-4 py-4 text-[13px] text-muted-foreground">
					{s.empty}
				</p>
			) : (
				<ul className="m-0 list-none p-0">
					{rows.map((p) => (
						<li
							key={p.id}
							data-testid="payment-row"
							className="grid gap-0.5 border-b border-border px-4 py-3 text-[13.5px] last:border-b-0"
						>
							<div className="flex flex-wrap items-baseline justify-between gap-2">
								<span className="font-semibold">{p.description}</span>
								<span className="font-mono tabular-nums">{formatMinor(p.gross_minor, "INR")}</span>
							</div>
							<div className="flex flex-wrap items-center justify-between gap-2 text-[12.5px] text-muted-foreground">
								<span>
									{formatDate(p.date)}
									{p.tax_minor > 0 ? ` · ${fill(s.gstNote, { tax: formatMinor(p.tax_minor, "INR") })}` : ""}
									{p.refunded_minor > 0
										? ` · ${fill(s.refunded, { amount: formatMinor(p.refunded_minor, "INR") })}`
										: ""}
								</span>
								<span className="flex items-center gap-2">
									<code className="text-[11.5px]">{p.reference}</code>
									<Badge variant={p.status === "captured" ? "ok" : "default"}>{s.status[p.status]}</Badge>
								</span>
							</div>
						</li>
					))}
				</ul>
			)}
		</section>
	);
}
