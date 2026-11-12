import { overviewStrings } from "@/lib/strings/overview";

/**
 * `usage_daily.source` values (api/routes/usage.py): `api` (POST /query), `stream`
 * (POST /query/stream), `conversation` (conversation messages) and `widget:<widget key id>`.
 * MCP agents call /query and /query/stream, so they are metered as `api` / `stream`.
 */
export type SourceKind = "widget" | "api" | "stream" | "conversation" | "eval" | "other";

export const SOURCE_ORDER: Exclude<SourceKind, "other">[] = ["widget", "api", "stream", "conversation", "eval"];

export function sourceKind(key: string | null): SourceKind {
	if (!key) return "other";
	if (key.startsWith("widget:")) return "widget";
	if (key === "api" || key === "stream" || key === "conversation" || key === "eval") return key;
	return "other";
}

/** The widget key id of a `widget:<id>` source, else null. */
export function widgetKeyId(key: string | null): string | null {
	return key?.startsWith("widget:") ? key.slice(7) || null : null;
}

export interface WidgetKeyName {
	name: string;
	kbName: string;
}

/**
 * A readable label: "Your apps (API)", "Your apps (streaming)", "Conversations",
 * "Widget · {KB} · {key name}"; an unknown widget key falls back to "Widget {id[:8]}".
 */
export function sourceLabel(key: string | null, widgetNames: ReadonlyMap<string, WidgetKeyName> = new Map()): string {
	const t = overviewStrings.sources;
	const kind = sourceKind(key);
	if (kind === "api") return t.api;
	if (kind === "stream") return t.stream;
	if (kind === "conversation") return t.conversation;
	if (kind === "eval") return t.eval;
	if (kind === "widget") {
		const id = widgetKeyId(key) ?? "";
		const named = widgetNames.get(id);
		return named ? `Widget · ${named.kbName} · ${named.name}` : `Widget ${id.slice(0, 8)}`;
	}
	// The new-lead summary the worker writes (conversation C4): spend, not a question.
	if (key === "lead_summary") return t.leadSummary;
	if (key === "assistant") return t.assistantHelper;
	// COB, the built-in assistant: the questions this workspace asked it (and, for the operator, the sign-in page).
	if (key === "cob") return t.cob;
	if (key === "cob_public") return t.cobPublic;
	// LP2 landing
	if (key === "landing") return t.landing;
	// /LP2 landing
	return key ?? "—";
}

/** Requests per source kind (widget keys summed), for the Overview totals row. */
export function totalsBySource(rows: { key: string | null; requests: number }[]): Record<SourceKind, number> {
	const out: Record<SourceKind, number> = { widget: 0, api: 0, stream: 0, conversation: 0, eval: 0, other: 0 };
	for (const r of rows) out[sourceKind(r.key)] += r.requests;
	return out;
}
