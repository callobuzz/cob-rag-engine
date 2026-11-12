import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import { type ApiError, toApiError } from "@/lib/api/errors";
import { operatorStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { useRestoreOperatorWorkspace } from "./api";

/**
 * Shown on Customers when the operator's own workspace does not exist (they deleted it on purpose,
 * and it stays deleted across restarts): "Your workspace was deleted · Recreate it", with a confirm.
 */
export function RestoreWorkspaceCard() {
	const [confirming, setConfirming] = useState(false);
	const restore = useRestoreOperatorWorkspace();
	const confirm = () =>
		restore.mutate(undefined, {
			onSuccess: (ws) => {
				setConfirming(false);
				toast.success(t.restoreToast, { description: fill(t.restoreToastBody, { name: ws.name }) });
			},
		});
	return (
		<section
			aria-label={t.restoreTitle}
			data-testid="restore-workspace-card"
			className="flex flex-wrap items-center gap-3 rounded-lg border border-dashed border-border bg-card p-3.5"
		>
			<div className="min-w-0 flex-1 basis-[260px]">
				<div className="font-semibold text-foreground">{t.restoreTitle}</div>
				<p className="m-0 mt-0.5 max-w-[640px] text-[12.5px] text-muted-foreground">{t.restoreBody}</p>
			</div>
			<Button size="sm" variant="default" onClick={() => setConfirming(true)}>
				{t.restore}
			</Button>
			<ConfirmDialog
				open={confirming}
				onOpenChange={(o) => {
					setConfirming(o);
					if (!o) restore.reset();
				}}
				title={t.restoreConfirmTitle}
				body={t.restoreConfirmBody}
				confirmLabel={t.restoreConfirm}
				destructive={false}
				pending={restore.isPending}
				onConfirm={confirm}
			>
				{restore.error ? <RestoreError error={toApiError(restore.error)} /> : null}
			</ConfirmDialog>
		</section>
	);
}

function RestoreError({ error }: { error: ApiError }) {
	return (
		<p role="alert" className="m-0 text-[13px] font-medium text-destructive">
			{error.code === "operator_workspace_exists" ? t.restoreExists : messageFor(error).title}
		</p>
	);
}
