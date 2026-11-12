import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { PasswordField } from "@/components/auth/PasswordField";
import { KeyValue } from "@/components/patterns/KeyValue";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { toApiError } from "@/lib/api/errors";
import { infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { can } from "@/lib/permissions";
import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { type CorpusDetail, type EmbeddingEntry, embeddingCatalogQuery, useUpdateCorpus } from "../api";
import { embeddingValue } from "../CreateKbDialog";
import { effectiveEmbedding, embeddingPair, isMultilingual } from "../embedding";
import { KbErrorAlert } from "../KbErrorAlert";
import { FormFoot, Panel, SettingsGrid } from "../ui";

/** Providers served by this engine itself; anything else may need its own key. */
const LOCAL_PROVIDERS = new Set(["local", "tei"]);

/** Embedding (mockup `t-embedding`): change → blue-green migration (owner-gated). */
export function EmbeddingTab({ kb }: { kb: CorpusDetail }) {
	const principal = usePrincipal();
	const owner = can(principal, "kb.changeEmbedding");
	const catalog = useQuery(embeddingCatalogQuery);
	const info = useQuery(infoQuery);
	const update = useUpdateCorpus(kb.id);
	const navigate = useNavigate();
	const ids = { model: useId(), key: useId() };

	const configured = embeddingPair(kb.embedding_config);
	const applied = embeddingPair(kb.applied_embedding);
	const effective = effectiveEmbedding(kb, info.data);
	const isApplied =
		!configured || (applied !== null && applied.provider === configured.provider && applied.model === configured.model);
	const currentValue = effective ? embeddingValue(effective.provider, effective.model) : "";

	const [choice, setChoice] = useState(currentValue);
	const [apiKey, setApiKey] = useState("");
	const [conflict, setConflict] = useState<unknown>(null);
	const value = choice || currentValue;
	const chosen = (catalog.data ?? []).find((m) => embeddingValue(m.provider, m.model) === value);
	const needsKey = !!chosen?.provider && !LOCAL_PROVIDERS.has(chosen.provider);
	const unchanged = value === currentValue;
	const multilingual = (catalog.data ?? []).find(isMultilingual);
	const showHint = multilingual && embeddingValue(multilingual.provider, multilingual.model) !== currentValue;

	const change = async () => {
		if (!chosen?.provider || !chosen.model || unchanged) return;
		setConflict(null);
		try {
			const next = await update.mutateAsync({
				embedding_config: {
					provider: chosen.provider,
					model: chosen.model,
					...(apiKey.trim() ? { api_key: apiKey.trim() } : {}),
				},
			});
			setApiKey("");
			if (next.migration_id) {
				const id = next.migration_id;
				toast.success(t.embedding.migrationToast, {
					action: {
						label: t.embedding.migrationLink,
						onClick: () => void navigate({ to: "/migrations", search: { id } }),
					},
				});
			} else toast.success(t.embedding.noMigrationToast);
		} catch (e) {
			const err = toApiError(e);
			setConflict(err);
		}
	};

	const label = (m: EmbeddingEntry) =>
		[m.name ?? m.model, m.dimensions ? `${m.dimensions} dims` : null, m.provider, m.notes].filter(Boolean).join(" · ");

	return (
		<SettingsGrid>
			<Panel
				title={t.embedding.title}
				sub={t.embedding.sub}
				aside={
					isApplied ? (
						<Badge variant="ok">{t.embedding.applied}</Badge>
					) : (
						<span className="flex items-center gap-2">
							<Badge variant="warn">{t.embedding.pending}</Badge>
							<Link to="/migrations" className="text-[12.5px] font-semibold text-brand-text">
								{t.embedding.pendingLink}
							</Link>
						</span>
					)
				}
			>
				<div className="grid gap-4 p-4">
					{conflict ? <KbErrorAlert error={conflict} /> : null}
					<div className="rounded-md border border-border">
						<KeyValue
							rows={[
								{
									label: t.embedding.current,
									value: configured
										? `${configured.model} · ${configured.provider}`
										: fill(t.embedding.engineDefault, { model: effective?.model ?? "…" }),
								},
							]}
						/>
					</div>
					<Field id={ids.model} label={t.embedding.model} hint={t.embedding.modelHint}>
						<NativeSelect
							id={ids.model}
							value={value}
							disabled={!owner || catalog.isError}
							onChange={(e) => setChoice(e.target.value)}
							aria-describedby={describedBy(ids.model, { hint: true })}
						>
							{!chosen && currentValue ? <option value={currentValue}>{effective?.model}</option> : null}
							{(catalog.data ?? []).map((m) => (
								<option key={`${m.provider}|${m.model}`} value={embeddingValue(m.provider, m.model)}>
									{label(m)}
								</option>
							))}
						</NativeSelect>
					</Field>
					{catalog.isError ? <FormAlert tone="warn">{t.embedding.catalogError}</FormAlert> : null}
					{needsKey && !unchanged ? (
						<Field id={ids.key} label={t.embedding.apiKey} hint={t.embedding.apiKeyHint}>
							<PasswordField
								id={ids.key}
								autoComplete="new-password"
								value={apiKey}
								onChange={(e) => setApiKey(e.target.value)}
								aria-describedby={describedBy(ids.key, { hint: true })}
							/>
						</Field>
					) : null}
				</div>
				<FormFoot>
					<OwnerGate action="kb.changeEmbedding">
						<Button
							type="button"
							variant="default"
							disabled={owner && (unchanged || update.isPending)}
							title={unchanged ? t.embedding.unchanged : undefined}
							onClick={() => void change()}
						>
							{t.embedding.change}
						</Button>
					</OwnerGate>
				</FormFoot>
			</Panel>
			{showHint && multilingual ? (
				<FormAlert tone="info">
					{fill(t.embedding.hint, { model: multilingual.name ?? multilingual.model ?? "" })}
				</FormAlert>
			) : null}
		</SettingsGrid>
	);
}
