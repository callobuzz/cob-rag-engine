import { createFileRoute } from "@tanstack/react-router";
import { MigrationsPage } from "@/features/migrations/MigrationsPage";
import { migrationsPageSearchSchema } from "@/features/migrations/search";

// `?id=` is linked from D3's Embedding tab.
export const Route = createFileRoute("/_app/migrations")({
	validateSearch: migrationsPageSearchSchema,
	component: MigrationsPage,
});
