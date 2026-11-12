import { useBlocker } from "@tanstack/react-router";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { kbsStrings as t } from "@/lib/strings/kbs";

/**
 * "Discard changes?" when leaving a dirty tab (another tab, another page). Browser unload is
 * blocked too. Returns the dialog to render.
 */
export function useUnsavedGuard(dirty: boolean) {
	const blocker = useBlocker({
		shouldBlockFn: ({ current, next }) => {
			if (!dirty) return false;
			const tabOf = (s: unknown) => (s as { tab?: string } | undefined)?.tab ?? "overview";
			// The Assistant tab has sections of its own (`?section=`): leaving one also needs a decision.
			const sectionOf = (s: unknown) => (s as { section?: string } | undefined)?.section ?? "overview";
			return (
				current.pathname !== next.pathname ||
				tabOf(current.search) !== tabOf(next.search) ||
				sectionOf(current.search) !== sectionOf(next.search)
			);
		},
		enableBeforeUnload: () => dirty,
		withResolver: true,
	});
	return (
		<ConfirmDialog
			open={blocker.status === "blocked"}
			onOpenChange={(o) => {
				if (!o && blocker.status === "blocked") blocker.reset();
			}}
			title={t.detail.discardTitle}
			body={t.detail.discardBody}
			confirmLabel={t.detail.discardConfirm}
			onConfirm={() => {
				if (blocker.status === "blocked") blocker.proceed();
			}}
		/>
	);
}

/** A failed save that isn't shown on a field: owner_required and friends become a toast. */
export function toastSaveError(e: unknown): void {
	const err = toApiError(e);
	const msg = messageFor(err);
	toast.error(msg.title, msg.body ? { description: msg.body } : undefined);
}

/** Map `"llm_config.temperature"` / `"retrieval_config.final_k"` 422 paths to a field name. */
export function fieldFromPath(path: string, section: string): string | null {
	const prefix = `${section}.`;
	return path.startsWith(prefix) ? (path.slice(prefix.length).split(".")[0] ?? null) : null;
}
