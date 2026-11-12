import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { Layers } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { actorFields, pk, portal, type Workspace } from "@/features/portal/api";
import { portalMessage } from "@/features/portal/messages";
import { api, unwrap } from "@/lib/api/client";
import { toApiError } from "@/lib/api/errors";
import { infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";

const b = t.reviewButton;

/** The open review of `versionId` in this knowledge base, if any (to link a `review_exists` refusal). */
async function findOpenReview(kb: string, versionId: string): Promise<Workspace | null> {
	const res = await unwrap(
		api().GET("/api/v1/portal/workspaces", { params: { query: { corpus_id: kb, status: "in_review", limit: 100 } } }),
	);
	return res.items.find((w) => w.reviewed_version_id === versionId && !w.finalized_version_id) ?? null;
}

/**
 * "Review in Content Portal" on a document's detail (D11, mounted in D4's header when the portal is
 * on): `POST /portal/workspaces/from-ingestion` for the active version, then opens the workspace.
 * 409 `review_exists` offers the open review (its `workspace_id`, else found in the listing);
 * `version_not_active` says only the active one can be.
 */
export function ReviewInPortalButton({
	kb,
	versionId,
	name,
}: {
	kb: string;
	versionId: string | null | undefined;
	name: string;
}) {
	const info = useQuery(infoQuery);
	const principal = usePrincipal();
	const navigate = useNavigate();
	const qc = useQueryClient();
	const [busy, setBusy] = useState(false);
	if (!info.data?.features.content_portal || !versionId) return null;

	const open = (id: string) =>
		void navigate({ to: "/portal/$workspaceId", params: { workspaceId: id }, search: { kb } });
	const start = async () => {
		setBusy(true);
		try {
			const res = await portal.fromIngestion(versionId, fill(b.titleOf, { name }), actorFields(principal));
			await qc.invalidateQueries({ queryKey: pk.lists });
			toast.success(fill(b.created, { n: res.chunks_copied }));
			open(res.workspace_id);
		} catch (e) {
			const err = toApiError(e);
			if (err.code === "review_exists") {
				// The refusal names the open review (workspace_id); older engines don't, so look it up.
				const named = typeof err.extra.workspace_id === "string" ? err.extra.workspace_id : null;
				const existing = named ?? (await findOpenReview(kb, versionId).catch(() => null))?.id ?? null;
				toast.error(
					b.exists,
					existing ? { action: { label: b.openExisting, onClick: () => open(existing) } } : undefined,
				);
			} else {
				const m = portalMessage(err);
				toast.error(m.title, m.body ? { description: m.body } : undefined);
			}
		} finally {
			setBusy(false);
		}
	};

	return (
		<Button variant="outline" disabled={busy} onClick={() => void start()}>
			<Layers aria-hidden />
			<span className="max-[520px]:sr-only">{busy ? b.creating : b.label}</span>
		</Button>
	);
}
