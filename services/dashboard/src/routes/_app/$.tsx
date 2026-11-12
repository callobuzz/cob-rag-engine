import { createFileRoute } from "@tanstack/react-router";
import { NotFoundPage } from "@/components/shell/NotAvailable";

/** Any unknown path: Not found, inside the shell (signed-out people are sent to /login first). */
export const Route = createFileRoute("/_app/$")({
	component: NotFoundPage,
});
