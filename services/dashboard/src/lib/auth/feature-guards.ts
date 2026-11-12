import type { QueryClient } from "@tanstack/react-query";
import { notFound } from "@tanstack/react-router";
import { infoQuery } from "@/lib/api/queries";

/** Content Portal routes exist only when GET /info says `features.content_portal`. */
export async function requireContentPortal(queryClient: QueryClient): Promise<void> {
	const info = await queryClient.ensureQueryData(infoQuery).catch(() => null);
	if (!info?.features.content_portal) throw notFound();
}
