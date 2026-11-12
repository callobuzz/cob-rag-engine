import { Download, Eye, Printer } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { type Currency, formatMinor } from "@/lib/format/money";
import { planBillingStrings } from "@/lib/strings/billing-statements";
import { fill } from "@/lib/strings/en";
import { monthLabel, openStatementPage, saveStatementCsv } from "./open";

const s = planBillingStrings.statements;

export interface StatementItem {
	month: string;
	closing_minor: number;
	live: boolean;
}

/** Where a statement's page and CSV come from: the owner's route or the admin's. */
export interface StatementSource {
	load: (month: string, format: "csv" | "html") => Promise<string>;
	/** File name for the CSV of `month`. */
	fileName: (month: string) => string;
}

/**
 * Months with their closing balance and three actions: View (a new tab), CSV, and Download PDF
 * (prints the same page; the browser saves it as PDF). The current month says "So far". Used on
 * the owner's Plan & billing page and on a customer's Billing tab.
 */
export function StatementsList({
	items,
	currency,
	source,
}: {
	items: StatementItem[];
	currency: Currency;
	source: StatementSource;
}) {
	const [busy, setBusy] = useState<string | null>(null);
	const run = async (key: string, action: () => Promise<unknown>) => {
		setBusy(key);
		try {
			const opened = await action();
			if (opened === false) toast.error(s.popup);
		} catch (e) {
			toast.error(s.loadError, { description: messageFor(toApiError(e)).title });
		} finally {
			setBusy(null);
		}
	};
	return (
		<ul data-testid="statements" className="m-0 grid list-none p-0">
			{items.map((it) => {
				const label = monthLabel(it.month);
				return (
					<li
						key={it.month}
						data-testid="statement-row"
						className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b border-dashed border-border px-4 py-2.5 last:border-b-0"
					>
						<div className="min-w-0">
							<div className="flex flex-wrap items-center gap-2 text-[13.5px] font-semibold">
								{label}
								{it.live ? <Badge variant="info">{s.soFar}</Badge> : null}
							</div>
							<div className="text-[12.5px] text-muted-foreground">
								{s.columns.closing}{" "}
								<span className="font-mono tabular-nums text-foreground">
									{formatMinor(it.closing_minor, currency)}
								</span>
							</div>
						</div>
						<div className="flex flex-wrap gap-1.5">
							<Button
								type="button"
								size="sm"
								variant="ghost"
								disabled={busy !== null}
								aria-label={fill(s.viewLabel, { month: label })}
								onClick={() =>
									void run(`view-${it.month}`, () =>
										openStatementPage(() => source.load(it.month, "html"), { print: false }),
									)
								}
							>
								<Eye aria-hidden className="size-4" />
								{s.view}
							</Button>
							<Button
								type="button"
								size="sm"
								variant="ghost"
								disabled={busy !== null}
								aria-label={fill(s.csvLabel, { month: label })}
								onClick={() =>
									void run(`csv-${it.month}`, async () =>
										saveStatementCsv(await source.load(it.month, "csv"), source.fileName(it.month)),
									)
								}
							>
								<Download aria-hidden className="size-4" />
								{s.csv}
							</Button>
							<Button
								type="button"
								size="sm"
								variant="ghost"
								disabled={busy !== null}
								aria-label={fill(s.pdfLabel, { month: label })}
								onClick={() =>
									void run(`pdf-${it.month}`, () =>
										openStatementPage(() => source.load(it.month, "html"), { print: true }),
									)
								}
							>
								<Printer aria-hidden className="size-4" />
								{s.pdf}
							</Button>
						</div>
					</li>
				);
			})}
		</ul>
	);
}
