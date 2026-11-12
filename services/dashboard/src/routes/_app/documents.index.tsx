import { createFileRoute } from "@tanstack/react-router";
import { DocumentsPage } from "@/features/documents/DocumentsPage";
import { documentsSearchSchema } from "@/lib/search";

export const Route = createFileRoute("/_app/documents/")({
	validateSearch: documentsSearchSchema,
	component: DocumentsPage,
});
