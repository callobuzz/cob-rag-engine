import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Plan } from "@/lib/api/accounts-types";
import type { PlanPricing } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises";
import { useCancelScheduledPricing } from "../api";
import { pricingSummary } from "../fields";
import { istDay, longDay } from "./ScheduleDialog";

const s = noSurprisesStrings.pricing;

/**
 * A price change that was announced and has not started (TV3): a "Scheduled" chip, what the new
 * price will be and from when, and a button to call it off (the businesses are told).
 */
export function ScheduledChange({ plan, scheduled }: { plan: Plan; scheduled: PlanPricing }) {
	const cancel = useCancelScheduledPricing();
	const [confirming, setConfirming] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const when = scheduled.effective_at ? longDay(istDay(scheduled.effective_at)) : "";
	const run = async () => {
		setError(null);
		try {
			await cancel.mutateAsync(plan.id);
			toast.success(s.cancelled, { description: fill(s.cancelledBody, { name: plan.name }) });
			setConfirming(false);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	return (
		<div
			data-testid="scheduled-change"
			className="grid gap-1.5 rounded-md border border-warning/40 bg-warning/10 p-2.5"
		>
			<div className="flex flex-wrap items-center gap-2">
				<Badge variant="warn" data-testid="scheduled-chip">
					{s.scheduledChip}
				</Badge>
				<span className="text-[13px] font-semibold">
					{fill(s.scheduledLine, { summary: pricingSummary(scheduled), date: when })}
				</span>
			</div>
			<div>
				<Button size="sm" variant="ghost" data-testid="scheduled-cancel" onClick={() => setConfirming(true)}>
					{s.cancel}
				</Button>
			</div>
			<ConfirmDialog
				open={confirming}
				onOpenChange={setConfirming}
				title={s.cancelConfirmTitle}
				body={fill(s.cancelConfirmBody, { name: plan.name })}
				confirmLabel={cancel.isPending ? s.cancelling : s.cancel}
				pending={cancel.isPending}
				onConfirm={() => void run()}
			>
				{error ? <p className="m-0 px-1 text-[13px] text-destructive">{error}</p> : null}
			</ConfirmDialog>
		</div>
	);
}
