import { createFileRoute } from "@tanstack/react-router";
import { z } from "zod";
import { DocsLayout } from "@/features/docs/DocsLayout";

/** `/docs` and `/docs/<page slug>` (the splat), `?q=` for a search, `#anchor` for a section. */
const docsSearchSchema = z.object({
	q: z.string().max(200).optional().catch(undefined),
});

export const Route = createFileRoute("/_app/docs/$")({
	validateSearch: docsSearchSchema,
	component: DocsLayout,
});
