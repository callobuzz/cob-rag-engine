import { toast } from "sonner";
import { lockHolder, type MessageContext, messageFor, type UiMessage } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { portalStrings } from "@/lib/strings/portal";

const t = portalStrings.errors;

/**
 * Portal errors → UI text (D11 "API endpoints and errors"). Coded errors come from the shared map;
 * this adds what depends on where the error happened: `version_not_active` and
 * `invalid_status_transition` on finalize, the lock refusals (423 on lock, the chunk editor's
 * uncoded 409 "not locked by you"), a provider down on chat and storage down on uploads.
 */
export type PortalAction = "finalize" | "lock" | "edit" | "chat" | "upload" | "other";

export function portalMessage(error: unknown, action: PortalAction = "other", ctx: MessageContext = {}): UiMessage {
	const e = toApiError(error);
	if (action === "finalize" && e.code === "version_not_active") return { title: t.versionChanged };
	if (action === "finalize" && e.code === "invalid_status_transition") return { title: t.already_published };
	if (e.status === 423) return { title: fill(t.locked, { holder: lockHolder(e.detail) ?? t.someone }) };
	if (e.status === 409 && !e.code && /\block/i.test(e.detail)) {
		const holder = lockHolder(e.detail);
		return { title: holder && !/not locked/i.test(e.detail) ? fill(t.locked, { holder }) : t.notLocked };
	}
	if (e.status === 503 && !e.code) {
		if (action === "chat") return { title: t.providerDown };
		if (action === "upload") return { title: t.storage };
	}
	return messageFor(e, ctx);
}

/** A failed portal write as an error toast. */
export function toastPortalError(error: unknown, action: PortalAction = "other") {
	const m = portalMessage(error, action);
	toast.error(m.title, m.body ? { description: m.body } : undefined);
}
