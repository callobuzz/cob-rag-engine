import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { KeyRound } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { useWatch } from "react-hook-form";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { PasswordField } from "@/components/auth/PasswordField";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AnswerModelPicker } from "@/features/benchmarks/AnswerModelPicker";
import {
	type AnswerModelResult,
	type FastModelResult,
	fastBenchmarksQuery,
	findResult,
	friendlyName,
	parseHosts,
} from "@/features/benchmarks/api";
import { FastModelPicker } from "@/features/benchmarks/FastModelPicker";
import { BelowBar } from "@/features/benchmarks/parts";
import { OwnKeyLock, useOwnKeyStatus } from "@/features/models/OwnKeyLock"; // P5 plans
import { infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { formatMoney } from "@/lib/format/currency";
import { formatCompact, formatNumber } from "@/lib/format/number";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { can } from "@/lib/permissions";
import { benchmarksStrings as bt } from "@/lib/strings/benchmarks";
import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { type CorpusDetail, modelCatalogQuery, usageQuery, useUpdateCorpus, utcDay } from "../api";
import { PROVIDER_SUGGESTIONS } from "../CreateKbDialog";
import { KbErrorAlert } from "../KbErrorAlert";
import { type LlmConfigForm, llmConfigSchema, numOrNull, strOrNull } from "../schemas";
import { Form2, FormFoot, Panel, SettingsGrid } from "../ui";
import { KeyOrder } from "./KeyOrder";
import { LanguageModels, languageFormFrom, languageModelsToSend } from "./LanguageModels";
import { fieldFromPath, toastSaveError, useUnsavedGuard } from "./shared";

const TEXT_FIELDS = ["provider", "model", "fast_model", "base_url"] as const;
const THINKING = ["off", "minimal", "low", "medium", "high", "provider-default"] as const;
type Thinking = (typeof THINKING)[number];
/** The older `reasoning_effort` names, still honoured by the engine ("none" = off). */
const OLD_EFFORT: Record<string, Thinking> = { none: "off", low: "low", medium: "medium", high: "high" };

function thinkingOf(v: unknown, old?: unknown): LlmConfigForm["thinking"] {
	if (typeof v === "string" && (THINKING as readonly string[]).includes(v)) return v as Thinking;
	return typeof old === "string" ? (OLD_EFFORT[old] ?? "") : "";
}
const NUM_FIELDS = ["temperature", "max_tokens"] as const;

function str(v: unknown): string {
	return typeof v === "string" || typeof v === "number" ? String(v) : "";
}

function formFrom(kb: CorpusDetail): LlmConfigForm {
	const c = kb.llm_config ?? {};
	return {
		provider: str(c.provider),
		model: str(c.model),
		fast_model: str(c.fast_model),
		fast_provider_order: Array.isArray(c.fast_provider_order) ? c.fast_provider_order.map(String).join(", ") : "",
		thinking: thinkingOf(c.thinking, c.reasoning_effort),
		fast_thinking: thinkingOf(c.fast_thinking),
		base_url: str(c.base_url),
		temperature: str(c.temperature),
		max_tokens: str(c.max_tokens),
		api_key: "",
		...languageFormFrom(c.language_models),
	};
}

/** Model & keys (mockup `t-model`): the customer's own model and key (write-only). Owner-gated. */
export function ModelTab({ kb }: { kb: CorpusDetail }) {
	const principal = usePrincipal();
	const owner = can(principal, "kb.changeModel");
	const info = useQuery(infoQuery);
	const catalog = useQuery(modelCatalogQuery);
	const update = useUpdateCorpus(kb.id);
	const [replacing, setReplacing] = useState(false);
	const [clearing, setClearing] = useState(false);
	const ids = {
		provider: useId(),
		model: useId(),
		fast: useId(),
		hosts: useId(),
		thinking: useId(),
		fastThinking: useId(),
		base: useId(),
		temp: useId(),
		max: useId(),
		key: useId(),
		providers: useId(),
		models: useId(),
	};

	const form = useAppForm<LlmConfigForm>({ resolver: zodResolver(llmConfigSchema), defaultValues: formFrom(kb) });
	const errs = form.formState.errors;
	const dirty = form.formState.isDirty;
	const guard = useUnsavedGuard(dirty);
	const provider = useWatch({ control: form.control, name: "provider" });
	const mainModel = useWatch({ control: form.control, name: "model" });
	const fastModel = useWatch({ control: form.control, name: "fast_model" });
	const fastHosts = useWatch({ control: form.control, name: "fast_provider_order" });
	const hostList = useMemo(() => parseHosts(fastHosts), [fastHosts]);
	const bench = useQuery(fastBenchmarksQuery);
	const typedResult = findResult(bench.data?.benchmarks?.models, fastModel.trim(), hostList);
	const pickFast = (m: FastModelResult) => {
		form.setValue("fast_model", m.model, { shouldDirty: true, shouldTouch: true });
		form.setValue("fast_provider_order", m.provider_order.join(", "), { shouldDirty: true, shouldTouch: true });
	};
	const pickMain = (m: AnswerModelResult) => form.setValue("model", m.model, { shouldDirty: true, shouldTouch: true });
	/** Saves just the budget model and its hosts through the same update the form uses; other edits stay. */
	const switchBudget = async (m: FastModelResult) => {
		try {
			const next = await update.mutateAsync({
				llm_config: {
					fast_model: m.model,
					fast_provider_order: m.provider_order.length > 0 ? m.provider_order : null,
				},
			});
			form.reset(formFrom(next), { keepDirtyValues: true });
			toast.success(fill(bt.budgetCallout.switched, { name: friendlyName(m.model) }), { description: m.model });
		} catch (e) {
			toastSaveError(e);
		}
	};
	const keySet = kb.llm_config?.api_key_encrypted === "***";
	const ownKey = useOwnKeyStatus(); // P5 plans
	const locked = ownKey != null && !ownKey.allowed;

	const modelOptions = useMemo(() => {
		const all = catalog.data?.models ?? [];
		const p = provider.trim().toLowerCase();
		const list = p ? all.filter((m) => m.id.toLowerCase().startsWith(`${p}/`) || m.provider.toLowerCase() === p) : all;
		return list.slice(0, 300);
	}, [catalog.data, provider]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const d = form.formState.dirtyFields;
			const llm: Record<string, string | number | string[] | Record<string, string> | null> = {};
			for (const k of TEXT_FIELDS) if (d[k]) llm[k] = strOrNull(v[k]);
			for (const k of NUM_FIELDS) if (d[k]) llm[k] = numOrNull(v[k]);
			if (d.fast_provider_order) {
				const hosts = parseHosts(v.fast_provider_order);
				llm.fast_provider_order = hosts.length > 0 ? hosts : null;
			}
			if (d.thinking) {
				llm.thinking = v.thinking === "" ? null : v.thinking;
				// The older name would otherwise still apply once `thinking` is cleared.
				if (kb.llm_config?.reasoning_effort != null) llm.reasoning_effort = null;
			}
			if (d.fast_thinking) llm.fast_thinking = v.fast_thinking === "" ? null : v.fast_thinking;
			if (d.lang_mode || d.lang_en || d.lang_hi || d.lang_hi_latn) {
				llm.language_models = languageModelsToSend(v, {
					stored: kb.llm_config?.language_models,
					engine: info.data?.llm.language_models ?? {},
					kbModel: str(kb.llm_config?.model),
					mainModel: str(kb.llm_config?.model) || (info.data?.llm.model ?? ""),
				});
			}
			if (replacing && v.api_key.trim()) llm.api_key = v.api_key.trim();
			if (Object.keys(llm).length === 0) return;
			const next = await update.mutateAsync({ llm_config: llm });
			form.reset(formFrom(next));
			setReplacing(false);
			toast.success(fill(t.detail.savedToast, { what: t.model.savedWhat }), {
				description: next.llm_config?.model ? String(next.llm_config.model) : undefined,
			});
		},
		{ fieldName: (path) => fieldFromPath(path, "llm_config") as keyof LlmConfigForm | null },
	);

	const clearKey = async () => {
		try {
			await update.mutateAsync({ llm_config: { api_key: null } });
			setClearing(false);
			toast.success(t.model.clearedToast);
		} catch (e) {
			setClearing(false);
			toastSaveError(e);
		}
	};

	const readOnly = !owner;
	const tenant = kb.tenant_name;
	const text = (name: (typeof TEXT_FIELDS)[number], id: string, label: string, hint: string, list?: string) => (
		<Field
			id={id}
			label={label}
			labelExtra={
				name === "fast_model" || name === "base_url" ? (
					<small className="text-muted-foreground">{t.model.optional}</small>
				) : undefined
			}
			hint={hint}
			error={errs[name]?.message}
		>
			<Input
				id={id}
				list={list}
				className="font-mono"
				autoComplete="off"
				readOnly={readOnly}
				aria-invalid={!!errs[name]}
				aria-describedby={describedBy(id, { hint: true, error: !!errs[name] })}
				{...form.register(name)}
			/>
		</Field>
	);

	const thinkingSelect = (
		name: "thinking" | "fast_thinking",
		id: string,
		label: string,
		hint: string,
		engine: string | null,
	) => (
		<Field
			id={id}
			label={label}
			labelExtra={<small className="text-muted-foreground">{t.model.optional}</small>}
			hint={hint}
			error={errs[name]?.message}
		>
			<NativeSelect
				id={id}
				disabled={readOnly}
				aria-invalid={!!errs[name]}
				aria-describedby={describedBy(id, { hint: true, error: !!errs[name] })}
				{...form.register(name)}
			>
				<option value="">
					{fill(t.model.thinkingEngine, {
						v: engine
							? (t.model.thinkingOptions[engine] ?? engine)
							: (t.model.thinkingOptions["provider-default"] ?? ""),
					})}
				</option>
				{THINKING.map((e) => (
					<option key={e} value={e}>
						{t.model.thinkingOptions[e]}
					</option>
				))}
			</NativeSelect>
		</Field>
	);

	return (
		<SettingsGrid>
			<Panel title={t.model.title} sub={t.model.sub}>
				<form onSubmit={(e) => void submit.onSubmit(e)} noValidate>
					<div className="grid gap-4 p-4">
						{submit.error ? <KbErrorAlert error={submit.error} /> : null}
						<datalist id={ids.providers}>
							{PROVIDER_SUGGESTIONS.map((p) => (
								<option key={p} value={p} />
							))}
						</datalist>
						<datalist id={ids.models}>
							{modelOptions.map((m) => (
								<option key={m.id} value={m.id}>
									{m.name}
								</option>
							))}
						</datalist>
						<Form2>
							{text("provider", ids.provider, t.model.provider, t.model.providerHint, ids.providers)}
							{text(
								"model",
								ids.model,
								t.model.model,
								catalog.isError ? t.model.catalogError : t.model.modelHint,
								ids.models,
							)}
						</Form2>
						<AnswerModelPicker model={mainModel} readOnly={readOnly} onPick={pickMain} />
						<Form2>
							{thinkingSelect(
								"thinking",
								ids.thinking,
								t.model.thinking,
								t.model.thinkingHint,
								info.data?.llm.thinking ?? null,
							)}
						</Form2>
						<Form2>
							{text("fast_model", ids.fast, t.model.fastModel, t.model.fastModelHint, ids.models)}
							{thinkingSelect(
								"fast_thinking",
								ids.fastThinking,
								t.model.fastThinking,
								t.model.fastThinkingHint,
								info.data?.llm.fast_thinking ?? "off",
							)}
						</Form2>
						{typedResult && !typedResult.passes ? <BelowBar model={typedResult} /> : null}
						<Field
							id={ids.hosts}
							label={bt.hostField.label}
							labelExtra={<small className="text-muted-foreground">{bt.hostField.optional}</small>}
							hint={bt.hostField.hint}
							error={errs.fast_provider_order?.message}
						>
							<Input
								id={ids.hosts}
								className="font-mono"
								autoComplete="off"
								readOnly={readOnly}
								aria-invalid={!!errs.fast_provider_order}
								aria-describedby={describedBy(ids.hosts, { hint: true, error: !!errs.fast_provider_order })}
								{...form.register("fast_provider_order")}
							/>
						</Field>
						<FastModelPicker
							model={fastModel.trim()}
							hosts={hostList}
							readOnly={readOnly}
							onPick={pickFast}
							onSwitchBudget={(m) => void switchBudget(m)}
						/>
						<LanguageModels form={form} readOnly={readOnly} engine={info.data?.llm.language_models ?? null} />
						<Form2>{text("base_url", ids.base, t.model.baseUrl, t.model.baseUrlHint)}</Form2>
						{/* P5 plans: without the allowance the key form is a note and a link, not a form */}
						{locked ? (
							<OwnKeyLock keySaved={keySet || ownKey?.key_saved === true} onRemove={() => setClearing(true)} />
						) : (
							<div className="grid gap-1.5">
								<span className="text-[13px] font-semibold">{fill(t.model.ownKey, { tenant })}</span>
								<fieldset
									className="m-0 flex min-w-0 flex-wrap items-center gap-3 rounded-md border border-border bg-muted p-3"
									aria-label={fill(t.model.ownKey, { tenant })}
								>
									<KeyRound className={keySet ? "text-success" : "text-muted-foreground"} aria-hidden />
									<div className="min-w-[180px] flex-1">
										{keySet ? (
											<>
												<b>{t.model.keySet}</b>{" "}
												<Badge variant="ok" className="ml-1.5">
													{t.model.encrypted}
												</Badge>
												<small className="block text-muted-foreground">{fill(t.model.keySetNote, { tenant })}</small>
											</>
										) : (
											<>
												<b>{t.model.noKey}</b>
												<small className="block text-muted-foreground">
													{info.data?.llm.key_configured === false ? t.model.noKeyCaller : t.model.noKeyEngine}
												</small>
											</>
										)}
									</div>
									<OwnerGate action="kb.changeModel">
										<Button
											type="button"
											size="sm"
											onClick={() => {
												if (replacing) form.resetField("api_key");
												setReplacing((r) => !r);
											}}
										>
											{replacing ? t.model.cancelReplace : keySet ? t.model.replace : t.model.set}
										</Button>
									</OwnerGate>
									{keySet ? (
										<OwnerGate action="kb.changeModel">
											<Button type="button" size="sm" variant="destructive" onClick={() => setClearing(true)}>
												{t.model.clear}
											</Button>
										</OwnerGate>
									) : null}
								</fieldset>
								{replacing ? (
									<Field id={ids.key} label={t.model.newKey}>
										<PasswordField
											id={ids.key}
											autoComplete="new-password"
											placeholder={t.model.newKeyPlaceholder}
											className="font-mono"
											{...form.register("api_key")}
										/>
									</Field>
								) : null}
							</div>
						)}
						{/* /P5 plans */}
						<Form2>
							<Field
								id={ids.temp}
								label={t.model.temperature}
								hint={t.model.temperatureHint}
								error={errs.temperature?.message}
							>
								<Input
									id={ids.temp}
									inputMode="decimal"
									className="font-mono"
									readOnly={readOnly}
									aria-invalid={!!errs.temperature}
									aria-describedby={describedBy(ids.temp, { hint: true, error: !!errs.temperature })}
									{...form.register("temperature")}
								/>
							</Field>
							<Field
								id={ids.max}
								label={`${t.model.maxTokens} (${t.model.maxTokensUnit})`}
								hint={t.model.maxTokensHint}
								error={errs.max_tokens?.message}
							>
								<Input
									id={ids.max}
									inputMode="numeric"
									className="font-mono"
									readOnly={readOnly}
									aria-invalid={!!errs.max_tokens}
									aria-describedby={describedBy(ids.max, { hint: true, error: !!errs.max_tokens })}
									{...form.register("max_tokens")}
								/>
							</Field>
						</Form2>
					</div>
					<FormFoot>
						<Button
							type="button"
							variant="ghost"
							disabled={!dirty}
							onClick={() => {
								form.reset(formFrom(kb));
								setReplacing(false);
							}}
						>
							{t.detail.discard}
						</Button>
						<OwnerGate action="kb.changeModel">
							<SubmitButton variant="default" pending={submit.isSubmitting} pendingLabel={t.detail.saving}>
								{t.detail.save}
							</SubmitButton>
						</OwnerGate>
					</FormFoot>
				</form>
			</Panel>
			<div className="grid content-start gap-4">
				<KeyOrder tenant={tenant} />
				<WeekCard kbId={kb.id} />
				<FormAlert tone="info">{t.model.embeddingNote}</FormAlert>
			</div>
			<ConfirmDialog
				open={clearing}
				onOpenChange={setClearing}
				title={t.model.clearTitle}
				body={fill(t.model.clearBody, { tenant })}
				confirmLabel={t.model.clearConfirm}
				pending={update.isPending}
				onConfirm={() => void clearKey()}
			/>
			{guard}
		</SettingsGrid>
	);
}

/** "This knowledge base this week": answers, tokens in/out, ≈ cost (usage, 7 days). */
export function WeekCard({ kbId }: { kbId: string }) {
	const usage = useQuery(usageQuery({ corpus_id: kbId, group_by: "corpus", start: utcDay(6), end: utcDay(0) }));
	const tot = usage.data?.totals;
	return (
		<Panel title={t.model.weekTitle} sub={t.model.weekSub}>
			{usage.isError ? (
				<div className="p-4">
					<ErrorState error={usage.error} onRetry={() => void usage.refetch()} />
				</div>
			) : (
				<dl className="m-0 grid grid-cols-3">
					{[
						{ k: t.model.answers, v: formatNumber(tot?.requests), s: "" },
						{
							k: t.model.tokens,
							v: formatCompact(tot ? tot.prompt_tokens + tot.completion_tokens : null),
							s: tot
								? fill(t.model.tokensSplit, {
										in: formatCompact(tot.prompt_tokens),
										out: formatCompact(tot.completion_tokens),
									})
								: "",
						},
						{ k: t.model.cost, v: tot ? `≈ ${formatMoney(tot.cost_usd)}` : "—", s: t.model.estimate },
					].map((x) => (
						<div key={x.k} className="grid gap-0.5 border-r border-border px-4 py-3 last:border-r-0">
							<dt className="text-[12px] text-muted-foreground">{x.k}</dt>
							<dd className="m-0 font-display text-lg font-bold tabular-nums">{usage.isPending ? "…" : x.v}</dd>
							{x.s ? <dd className="m-0 text-[11.5px] text-muted-foreground">{x.s}</dd> : null}
						</div>
					))}
				</dl>
			)}
		</Panel>
	);
}
