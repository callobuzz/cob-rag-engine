import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import type { BillingAccount } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises";
import { useCancelPendingChange } from "../api";

const s = noSurprisesStrings.pending;

/**
 * A raise for this business that waits for its next renewal (TV3): when it applies, what moves
 * (old to new), and a button to cancel it. Nothing shows when none is pending.
 */
export function PendingChange({ account }: { account: BillingAccount }) {
	const cancel = useCancelPendingChange(account.tenant_id);
	const [confirming, setConfirming] = useState(false);
	const [error, setError] = useState<string | null>(null);
	const pending = account.pending_change;
	if (!pending) return null;
	const run = async () => {
		setError(null);
		try {
			await cancel.mutateAsync();
			toast.success(s.cancelled);
			setConfirming(false);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	return (
		<Panel title={s.title} sub={fill(s.lead, { date: formatDate(pending.effective_at) })}>
			<div data-testid="pending-change" className="grid gap-2 p-4">
				{pending.plan ? (
					<p className="m-0 text-[13.5px] font-semibold">{fill(s.toPlan, { plan: pending.plan.name })}</p>
				) : null}
				<ul className="m-0 grid list-none gap-1 p-0 text-[13.5px]">
					{(pending.changes as { label: string; old: string; new: string }[]).map((c) => (
						<li key={c.label}>{fill(s.row, { label: c.label, old: c.old, new: c.new })}</li>
					))}
				</ul>
				<div>
					<Button size="sm" variant="outline" data-testid="pending-cancel" onClick={() => setConfirming(true)}>
						{s.cancel}
					</Button>
				</div>
			</div>
			<ConfirmDialog
				open={confirming}
				onOpenChange={setConfirming}
				title={s.confirmTitle}
				body={fill(s.confirmBody, { name: account.tenant_name })}
				confirmLabel={s.cancel}
				pending={cancel.isPending}
				onConfirm={() => void run()}
			>
				{error ? <p className="m-0 px-1 text-[13px] text-destructive">{error}</p> : null}
			</ConfirmDialog>
		</Panel>
	);
}
