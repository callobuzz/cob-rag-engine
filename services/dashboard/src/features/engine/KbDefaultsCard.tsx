import { KeyValue } from "@/components/patterns/KeyValue";
import { RETRIEVAL_FIELDS } from "@/features/kbs/retrieval-fields";
import { Panel } from "@/features/kbs/ui";
import type { EngineInfo } from "@/lib/api/accounts-types";
import { engineStrings } from "@/lib/strings/admin";

const t = engineStrings.kbDefaults;

function label(key: string): string {
	return RETRIEVAL_FIELDS.find((f) => f.key === key)?.label ?? key.replace(/_/g, " ");
}

function value(key: string, v: unknown): string {
	if (typeof v === "boolean") return v ? t.on : t.off;
	if (v == null) return key === "rerank_llm_model" ? t.fastModel : "—";
	const field = RETRIEVAL_FIELDS.find((f) => f.key === key);
	if (field && field.control.kind === "select") {
		return field.control.options.find((o) => o.value === v)?.label ?? String(v);
	}
	return String(v);
}

/** `GET /info` `retrieval_defaults`: what a knowledge base uses for every field it leaves unset. */
export function KbDefaultsCard({ defaults }: { defaults: EngineInfo["retrieval_defaults"] | undefined }) {
	if (!defaults) return null;
	return (
		<Panel title={t.title} sub={t.sub}>
			<KeyValue
				rows={Object.entries(defaults).map(([k, v]) => ({
					label: label(k),
					value: value(k, v),
				}))}
			/>
		</Panel>
	);
}
