import { useQuery } from "@tanstack/react-query";
import { FormAlert } from "@/components/auth/FormAlert";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { LanesView } from "@/features/fair-use/LanesView"; // PIP4
import { SafetyRailsCard } from "@/features/fair-use/SafetyRailsCard"; // PIP4
import { Panel, SettingsGrid } from "@/features/kbs/ui";
import type { EngineInfo } from "@/lib/api/accounts-types";
import { formatBytes } from "@/lib/format/bytes";
import { formatNumber } from "@/lib/format/number";
import { engineStrings as t } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { languageModelsStrings as lm } from "@/lib/strings/language-models";
import { infoQuery } from "./api";
import { CobReviewList } from "./CobReviewList";
import { CobStatusCard } from "./CobStatusCard";
import { FastModelCard } from "./FastModelCard";
import { FeaturesCard } from "./FeaturesCard";
import { HealthCard } from "./HealthCard";
import { KbDefaultsCard } from "./KbDefaultsCard";
import { LocalModelCard } from "./LocalModelCard"; // PIP2 local
import { RetrievalDefaultsForm } from "./RetrievalDefaultsForm";

/** `/admin/engine` (G-12): every `GET /info` field, health and the engine-wide retrieval defaults. */
export function EnginePage() {
	const q = useQuery(infoQuery);
	return (
		<div className="grid gap-4">
			<PageHeader title={t.page.title} description={t.page.lead} />
			{q.isPending ? (
				<SettingsGrid>
					<CardSkeleton lines={6} />
					<CardSkeleton lines={6} />
				</SettingsGrid>
			) : q.isError ? (
				<ErrorState error={q.error} title={t.page.error} onRetry={() => void q.refetch()} />
			) : (
				<Info info={q.data} />
			)}
			<RetrievalDefaultsForm />
		</div>
	);
}

function Info({ info }: { info: EngineInfo }) {
	const e = info.embedding;
	const llm = info.llm;
	const l = info.limits;
	const languageRoutes = Object.entries(llm?.language_models ?? {}).map(([code, model]) =>
		fill(lm.engine.row, { lang: lm.rows[code]?.label ?? code, model }),
	);
	return (
		<SettingsGrid>
			<div className="grid min-w-0 content-start gap-4">
				<Panel title={t.about.title}>
					<KeyValue
						rows={[
							{ label: t.about.version, value: info.version },
							{ label: t.about.publicUrl, value: info.public_url },
						]}
					/>
					{info.public_url_configured ? null : (
						<div className="border-t border-border p-3">
							<FormAlert tone="warn">{t.about.notConfigured}</FormAlert>
						</div>
					)}
				</Panel>
				<Panel title={t.models.title}>
					<KeyValue
						rows={[
							{
								label: t.models.embedding,
								value: fill(t.models.embeddingValue, {
									model: e?.model ?? "—",
									provider: e?.provider ?? "—",
									dims: formatNumber(e?.dimensions),
								}),
							},
							{ label: t.models.llmProvider, value: llm?.provider ?? "—" },
							{ label: t.models.llmModel, value: llm?.model ?? "—" },
							{ label: t.models.fastModel, value: llm?.fast_model ?? t.models.sameAsModel, muted: !llm?.fast_model },
							{
								label: t.models.fastHosts,
								value: llm?.fast_provider_order?.length ? llm.fast_provider_order.join(", ") : t.models.fastHostsNone,
								muted: !llm?.fast_provider_order?.length,
							},
							{
								label: t.models.thinking,
								value: llm?.thinking ?? t.models.thinkingUnset,
								muted: !llm?.thinking,
							},
							{ label: t.models.fastThinking, value: llm?.fast_thinking ?? "off" },
							{
								label: lm.engine.label,
								value: languageRoutes.length ? languageRoutes.join(", ") : lm.engine.none,
								muted: !languageRoutes.length,
							},
							{ label: t.models.keySet, value: llm?.key_configured ? t.models.keyYes : t.models.keyNo },
						]}
					/>
				</Panel>
				<FastModelCard llm={llm} />
				<Panel title={t.limits.title}>
					<KeyValue
						rows={[
							{ label: t.limits.upload, value: formatBytes(l?.max_upload_bytes) },
							{
								label: t.limits.tokens,
								value: fill(t.limits.tokensValue, { n: formatNumber(l?.max_document_tokens) }),
							},
							{ label: t.limits.types, value: (l?.allowed_extensions ?? []).join(" ") },
							{ label: t.limits.mimeTypes, value: (l?.allowed_mime_types ?? []).join(", ") },
							{ label: t.limits.records, value: formatNumber(l?.max_records_per_request) },
							{ label: t.limits.recordChars, value: fill(t.limits.chars, { n: formatNumber(l?.max_record_chars) }) },
							{ label: t.limits.query, value: fill(t.limits.chars, { n: formatNumber(l?.max_query_chars) }) },
						]}
					/>
				</Panel>
			</div>
			<div className="grid min-w-0 content-start gap-4">
				<HealthCard />
				{/* PIP2 local */}
				<LocalModelCard />
				{/* /PIP2 local */}
				{/* PIP4 */}
				<SafetyRailsCard />
				<LanesView />
				{/* /PIP4 */}
				<CobStatusCard />
				<CobReviewList />
				<FeaturesCard features={info.features} />
				<KbDefaultsCard defaults={info.retrieval_defaults} />
			</div>
		</SettingsGrid>
	);
}
