import { z } from "zod";
import { migrationsSearchSchema as base } from "@/lib/search";

/** Every status the engine uses (migration/service.py), in lifecycle order. */
export const MIGRATION_STATUSES = [
	"pending_approval",
	"queued",
	"running",
	"verifying",
	"completed",
	"rolling_back",
	"rolled_back",
	"cancelled",
	"failed",
] as const;
export type MigrationStatus = (typeof MIGRATION_STATUSES)[number];

/** TERMINAL_STATUSES in migration/service.py: nothing moves any more, so polling stops. */
export const TERMINAL: ReadonlySet<string> = new Set(["completed", "rolled_back", "cancelled", "failed"]);
export const isTerminal = (status: string) => TERMINAL.has(status);

/**
 * `/migrations?kb=&status=&id=&page=` — `id` opens a migration (D3's Embedding tab links it).
 * Search params live with the feature (src/lib/search.ts is shared with epics in progress).
 */
export const migrationsPageSearchSchema = base.extend({
	kb: z.uuid().optional().catch(undefined),
	status: z.enum(MIGRATION_STATUSES).optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
});
export type MigrationsSearch = z.infer<typeof migrationsPageSearchSchema>;

export const MIGRATIONS_PAGE_SIZE = 50;
export const MIGRATION_POLL_MS = 5000;
