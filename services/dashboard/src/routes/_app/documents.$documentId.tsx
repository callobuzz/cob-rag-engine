import { createFileRoute } from "@tanstack/react-router";
import { DocumentDetailPage } from "@/features/documents/DocumentDetailPage";
import { documentDetailSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/documents/$documentId")({
	validateSearch: documentDetailSearchSchema,
	component: DocumentDetailPage,
});
