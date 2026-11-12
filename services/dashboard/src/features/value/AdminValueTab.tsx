import { useQuery } from "@tanstack/react-query";
import { Printer } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { openStatementPage } from "@/features/billing/statements/open";
import type { AdminTenant } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { valueStrings as t } from "@/lib/strings/value";
import { adminValueReportQuery, loadAdminValuePage } from "./api";
import { monthName } from "./format";
import { ValueReportView } from "./ValueReportView";

const MONTHS_SHOWN = 12;

/**
 * The customer detail's "Value" tab (deployer): the business's value report exactly as its owner
 * sees it, month by month, with the same printable page. Read only: the deployer cannot change a
 * business's values. A month before the business began is refused by the engine and said so here.
 */
export function AdminValueTab({ tenant }: { tenant: AdminTenant }) {
	// "" is this month: no month is sent, so the engine answers with its own live month (its time zone, not ours).
	const [months] = useState(recentMonths);
	const [month, setMonth] = useState("");
	const q = useQuery(adminValueReportQuery(tenant.id, month || undefined));
	const print = async () => {
		try {
			const opened = await openStatementPage(() => loadAdminValuePage(tenant.id, month || undefined), { print: false });
			if (!opened) toast.error(t.actions.printBlocked);
		} catch (e) {
			toast.error(t.actions.printFailed, { description: messageFor(toApiError(e)).title });
		}
	};
	return (
		<div className="grid gap-4" data-testid="admin-value">
			<div className="flex flex-wrap items-end justify-between gap-3">
				<div className="min-w-0">
					<h2 className="m-0 text-[15px] font-bold">{fill(t.admin.title, { business: tenant.name })}</h2>
					<p className="m-0 text-[12.5px] text-muted-foreground">{t.admin.lead}</p>
				</div>
				<div className="flex flex-wrap items-center gap-2">
					<label htmlFor="admin-value-month" className="text-[13px] font-semibold">
						{t.monthLabel}
					</label>
					<NativeSelect id="admin-value-month" value={month} onChange={(e) => setMonth(e.target.value)}>
						{months.map((m) => (
							<option key={m} value={m}>
								{m ? monthName(m) : t.thisMonthPlain}
							</option>
						))}
					</NativeSelect>
					<Button variant="outline" size="sm" onClick={() => void print()} disabled={!q.data}>
						<Printer aria-hidden />
						{t.actions.print}
					</Button>
				</div>
			</div>
			{q.isPending ? (
				<div className="grid gap-4" aria-busy="true">
					<CardSkeleton lines={4} />
					<CardSkeleton lines={4} />
				</div>
			) : q.isError ? (
				<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
			) : (
				<ValueReportView report={q.data} addAnswers={false} />
			)}
		</div>
	);
}

/** This month ("") and the earlier ones, newest first, as `YYYY-MM`. */
function recentMonths(): string[] {
	const now = new Date();
	const out: string[] = [""];
	for (let back = 1; back < MONTHS_SHOWN; back++) {
		const d = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth() - back, 1));
		out.push(`${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`);
	}
	return out;
}
