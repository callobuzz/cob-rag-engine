import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { Printer, SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { openStatementPage } from "@/features/billing/statements/open";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { fill } from "@/lib/strings/en";
import { valueStrings as t } from "@/lib/strings/value";
import { loadValuePage, valueReportQuery, valueReportsQuery, valueSettingsQuery } from "./api";
import { monthName } from "./format";
import { ValueReportView } from "./ValueReportView";
import { ValueSettingsDialog } from "./ValueSettingsDialog";

const route = getRouteApi("/_app/value");

/**
 * `/value?month=YYYY-MM`: what the assistant did this month (so far) or in a closed month, with
 * the estimate and its formula. Owners change the values behind the estimate; members read it.
 * Linked from the Home card and the monthly e-mail, not from the menu.
 */
export function ValuePage() {
	const { month, edit } = route.useSearch();
	const navigate = useNavigate();
	const principal = usePrincipal();
	const owner = principal?.role === "owner" || principal?.auth === "api_key";
	const report = useQuery(valueReportQuery(month));
	const frozen = useQuery(valueReportsQuery);
	const settings = useQuery({ ...valueSettingsQuery, enabled: owner });
	const [editing, setEditing] = useState(edit === 1);

	const header = (
		<PageHeader
			title={t.title}
			description={t.lead}
			actions={
				report.data ? (
					<div className="flex flex-wrap items-center gap-2">
						<Button variant="outline" size="sm" onClick={() => void print(report.data.month)} data-testid="value-print">
							<Printer aria-hidden />
							{t.actions.print}
						</Button>
						{owner ? (
							<Button
								variant="default"
								size="sm"
								disabled={!settings.data}
								onClick={() => setEditing(true)}
								data-testid="value-change"
							>
								<SlidersHorizontal aria-hidden />
								{t.actions.change}
							</Button>
						) : null}
					</div>
				) : null
			}
		/>
	);

	const print = async (m: string) => {
		try {
			const opened = await openStatementPage(() => loadValuePage(m), { print: false });
			if (!opened) toast.error(t.actions.printBlocked);
		} catch (e) {
			toast.error(t.actions.printFailed, { description: messageFor(toApiError(e)).title });
		}
	};

	if (report.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				{header}
				<CardSkeleton lines={4} />
				<CardSkeleton lines={4} />
			</div>
		);
	}
	if (report.isError) {
		return (
			<div className="grid gap-4">
				{header}
				<ErrorState error={report.error} title={t.loadError} onRetry={() => void report.refetch()} />
			</div>
		);
	}
	const data = report.data;
	// The first option is always the live month, whose value is "" (no month asked for: the engine decides
	// what "this month" is in the business's own time zone). Then the closed months.
	const options = [
		{ value: "", label: data.live ? fill(t.thisMonth, { month: monthName(data.month) }) : t.thisMonthPlain },
		...(frozen.data?.items ?? []).map((m) => ({ value: m.month, label: monthName(m.month) })),
	];
	if (!data.live && !options.some((o) => o.value === data.month)) {
		options.push({ value: data.month, label: monthName(data.month) });
	}
	return (
		<div className="grid gap-4">
			{header}
			<div className="flex items-center gap-2">
				<label htmlFor="value-month" className="text-[13px] font-semibold">
					{t.monthLabel}
				</label>
				<NativeSelect
					id="value-month"
					value={data.live ? "" : data.month}
					onChange={(e) => void navigate({ to: "/value", search: e.target.value ? { month: e.target.value } : {} })}
				>
					{options.map((o) => (
						<option key={o.value} value={o.value}>
							{o.label}
						</option>
					))}
				</NativeSelect>
			</div>
			<ValueReportView report={data} addAnswers />
			{!owner ? <p className="m-0 text-[12.5px] text-muted-foreground">{t.readOnly}</p> : null}
			{owner && settings.data ? (
				<ValueSettingsDialog
					open={editing}
					onOpenChange={(open) => {
						setEditing(open);
						if (!open && edit) void navigate({ to: "/value", search: { month }, replace: true });
					}}
					settings={settings.data}
				/>
			) : null}
		</div>
	);
}
