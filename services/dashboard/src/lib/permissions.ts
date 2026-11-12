import type { Principal } from "@/lib/auth/principal";

/**
 * The one owner/member gate (README "Principals, views and owner/member gating"; A5 owner-only
 * list). Only a MEMBER SESSION is refused; admin key, tk_ key, super admin and owner sessions
 * pass. The server stays the authority (`owner_required` is still handled).
 */
export const OWNER_ONLY_ACTIONS = [
	"kb.create",
	"kb.delete",
	"kb.rotateKey",
	"kb.changeModel",
	"kb.changeEmbedding",
	"kb.move",
	"document.delete",
	"document.merge",
	"widgetKey.manage",
	"workspace.rotateKey",
	"key.manage",
	"memory.bulkDelete",
	"golden.delete",
	"portal.deleteWorkspace",
	"team.manage",
	"assistant.publish",
	"assistant.debug",
	"eval.settings",
	"lead.delete",
	"lead.export",
	"website.manage",
	"whatsapp.manage",
] as const;

export type OwnerAction = (typeof OWNER_ONLY_ACTIONS)[number];

export function can(principal: Principal | null, _action: OwnerAction): boolean {
	if (!principal) return false;
	return !(principal.auth === "session" && principal.role === "member");
}

/** Tooltip / toast text for a locked control. */
export const LOCKED_TEXT: Record<OwnerAction, string> = {
	"kb.create": "Only owners can create knowledge bases",
	"kb.delete": "Only owners can delete knowledge bases",
	"kb.rotateKey": "Only owners can rotate keys",
	"kb.changeModel": "Only owners can set the model and its key",
	"kb.changeEmbedding": "Only owners can change the embedding model",
	"kb.move": "Only owners can move a knowledge base",
	"document.delete": "Only owners can delete documents",
	"document.merge": "Only owners can merge documents",
	"widgetKey.manage": "Only owners can manage widget keys",
	"workspace.rotateKey": "Only owners can rotate the workspace key",
	"key.manage": "Only owners can create, change, rotate or revoke keys",
	"memory.bulkDelete": "Only owners can delete memories in bulk",
	"golden.delete": "Only owners can delete golden questions",
	"portal.deleteWorkspace": "Only owners can delete portal workspaces",
	"team.manage": "Only owners can invite or change teammates",
	"assistant.publish": "Only owners can publish the assistant",
	"assistant.debug": "Only owners see the full trace",
	"eval.settings": "Only owners can change the judge or the pass marks",
	"lead.delete": "Only owners can delete leads",
	"lead.export": "Only owners can export leads",
	"website.manage": "Only owners can change what your assistant learns from your website",
	"whatsapp.manage": "Only owners can connect or change your WhatsApp number",
};
