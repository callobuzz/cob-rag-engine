import { useQuery } from "@tanstack/react-query";
import { type ReactNode, useId } from "react";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { AnswerModelPicker } from "@/features/benchmarks/AnswerModelPicker";
import { FastModelPicker } from "@/features/benchmarks/FastModelPicker";
import { localLlmQuery } from "@/features/engine/local-llm-api"; // PIP4
import { fill } from "@/lib/strings/en";
import { pipelinesStrings } from "@/lib/strings/pipelines";
import type { ProfileResolved } from "./api";
import {
	type FieldKey,
	LANGUAGE_CODES,
	nowLanguages,
	nowModel,
	nowTri,
	type ProfileForm,
	shortModel,
	withRoute,
} from "./format";
import { LocalModelPicker } from "./LocalModelPicker"; // PIP4

const t = pipelinesStrings.dialog;

type Errors = Partial<Record<FieldKey, string>>;

function Section({ title, lead, children }: { title: string; lead: string; children: ReactNode }) {
	return (
		<fieldset className="m-0 grid gap-3.5 rounded-md border border-border p-4">
			<legend className="px-1.5 text-[14px] font-bold">{title}</legend>
			<p className="m-0 -mt-1 text-[12.5px] text-muted-foreground">{lead}</p>
			{children}
		</fieldset>
	);
}

const follow = (now: string) => fill(t.follow, { now });

/**
 * The four plain sections of a profile (Answers, Search, Length and extras, Speed and fairness).
 * Every empty field says what it follows right now, from the profile's `resolved` block.
 */
export function ProfileFields({
	form,
	set,
	errors,
	resolved,
}: {
	form: ProfileForm;
	set: (patch: Partial<ProfileForm>) => void;
	errors: Errors;
	resolved: ProfileResolved | undefined;
}) {
	const id = useId();
	const i = (k: string) => `${id}-${k}`;
	const local = form.route === "local";
	// PIP4: the local model's own name, for "follow" and the picker
	const localStatus = useQuery({ ...localLlmQuery, enabled: local });
	const err = (k: FieldKey) => errors[k];
	const props = (k: string, key: FieldKey, hint = true) => ({
		id: i(k),
		"aria-invalid": err(key) ? (true as const) : undefined,
		"aria-describedby": describedBy(i(k), { hint, error: !!err(key) }),
	});

	return (
		<div className="grid gap-4">
			<Section title={t.sections.answers} lead={t.sectionLeads.answers}>
				<Field id={i("route")} label={t.route} hint={t.routeHelp}>
					<NativeSelect
						id={i("route")}
						aria-describedby={`${i("route")}-hint`}
						value={form.route}
						onChange={(e) => set(withRoute(form, e.target.value as ProfileForm["route"]))}
					>
						<option value="engine">{t.routes.engine}</option>
						<option value="local">{t.routes.local}</option>
					</NativeSelect>
				</Field>
				<Field
					id={i("model")}
					label={local ? t.answerModelLocal : t.answerModel}
					hint={`${t.answerModelHelp} ${fill(t.follow, { now: local ? (localStatus.data?.model ?? t.localNotReady) : nowModel(resolved) })}`}
					error={err("answerModel")}
				>
					<Input
						{...props("model", "answerModel")}
						className="font-mono text-[12.5px]"
						autoComplete="off"
						spellCheck={false}
						placeholder={follow(
							local ? (localStatus.data?.model ?? t.localNotReady) : shortModel(resolved?.answer_model),
						)}
						value={form.answerModel}
						onChange={(e) => set({ answerModel: e.target.value })}
					/>
				</Field>
				{local ? (
					<LocalModelPicker model={form.answerModel} onPick={(m) => set({ answerModel: m })} />
				) : (
					<AnswerModelPicker model={form.answerModel} readOnly={false} onPick={(m) => set({ answerModel: m.model })} />
				)}
				<Field id={i("thinking")} label={t.thinking} hint={t.thinkingHelp}>
					<NativeSelect
						id={i("thinking")}
						aria-describedby={`${i("thinking")}-hint`}
						value={form.thinking}
						onChange={(e) => set({ thinking: e.target.value })}
					>
						<option value="">{follow(resolved?.thinking ?? t.nowNothing)}</option>
						{Object.entries(t.thinkingOptions).map(([v, label]) => (
							<option key={v} value={v}>
								{label}
							</option>
						))}
					</NativeSelect>
				</Field>
				<Field id={i("lang")} label={t.languages} hint={t.languagesHelp} error={err("languages")}>
					<NativeSelect
						{...props("lang", "languages")}
						value={form.languageMode}
						onChange={(e) => set({ languageMode: e.target.value as ProfileForm["languageMode"] })}
					>
						<option value="follow">{`${t.languageModes.follow} (${fill(t.followNow, { now: nowLanguages(resolved) })})`}</option>
						<option value="none">{t.languageModes.none}</option>
						<option value="custom">{t.languageModes.custom}</option>
					</NativeSelect>
				</Field>
				{form.languageMode === "custom" ? (
					<div className="grid gap-2 sm:grid-cols-3">
						{LANGUAGE_CODES.map((c) => (
							<Field key={c} id={i(`lang-${c}`)} label={t.languageRows[c] ?? c}>
								<Input
									id={i(`lang-${c}`)}
									className="font-mono text-[12.5px]"
									autoComplete="off"
									spellCheck={false}
									placeholder={t.languageEmpty}
									value={form.languages[c] ?? ""}
									onChange={(e) => set({ languages: { ...form.languages, [c]: e.target.value } })}
								/>
							</Field>
						))}
					</div>
				) : null}
				<Field
					id={i("fast")}
					label={t.fastModel}
					hint={`${t.fastModelHelp} ${fill(t.follow, { now: shortModel(resolved?.fast_model) })}`}
				>
					<Input
						id={i("fast")}
						aria-describedby={`${i("fast")}-hint`}
						className="font-mono text-[12.5px]"
						autoComplete="off"
						spellCheck={false}
						placeholder={follow(shortModel(resolved?.fast_model))}
						value={form.fastModel}
						onChange={(e) => set({ fastModel: e.target.value })}
					/>
				</Field>
				{local ? null : (
					<FastModelPicker
						model={form.fastModel}
						hosts={[]}
						readOnly={false}
						onPick={(m) => set({ fastModel: m.model })}
					/>
				)}
			</Section>

			<Section title={t.sections.search} lead={t.sectionLeads.search}>
				<Field id={i("rerank")} label={t.reranker} hint={t.rerankerHelp}>
					<NativeSelect
						id={i("rerank")}
						aria-describedby={`${i("rerank")}-hint`}
						value={form.reranker}
						onChange={(e) => set({ reranker: e.target.value as ProfileForm["reranker"] })}
					>
						{Object.entries(t.rerankerOptions).map(([v, label]) => (
							<option key={v} value={v}>
								{label}
							</option>
						))}
					</NativeSelect>
				</Field>
				{form.reranker === "llm" || form.reranker === "api" ? (
					<Field
						id={i("rerankModel")}
						label={t.rerankModel}
						hint={form.reranker === "api" ? t.rerankModelHelpApi : t.rerankModelHelpLlm}
						error={err("rerankModel")}
					>
						<Input
							{...props("rerankModel", "rerankModel")}
							className="font-mono text-[12.5px]"
							autoComplete="off"
							spellCheck={false}
							placeholder={form.reranker === "api" ? "cohere/rerank-v3.5" : follow(shortModel(resolved?.rerank_model))}
							value={form.rerankModel}
							onChange={(e) => set({ rerankModel: e.target.value })}
						/>
					</Field>
				) : null}
				<div className="grid gap-3 sm:grid-cols-3">
					<Field
						id={i("topk")}
						label={t.topK}
						hint={`${t.topKHelp} ${fill(t.follow, { now: String(resolved?.top_k ?? "") })}`}
						error={err("topK")}
					>
						<Input
							{...props("topk", "topK")}
							inputMode="numeric"
							placeholder={follow(String(resolved?.top_k ?? ""))}
							value={form.topK}
							onChange={(e) => set({ topK: e.target.value })}
						/>
					</Field>
					<Field
						id={i("finalk")}
						label={t.finalK}
						hint={`${t.finalKHelp} ${fill(t.follow, { now: String(resolved?.final_k ?? "") })}`}
						error={err("finalK")}
					>
						<Input
							{...props("finalk", "finalK")}
							inputMode="numeric"
							placeholder={follow(String(resolved?.final_k ?? ""))}
							value={form.finalK}
							onChange={(e) => set({ finalK: e.target.value })}
						/>
					</Field>
					<Field
						id={i("rewrites")}
						label={t.rewrites}
						hint={`${t.rewritesHelp} ${fill(t.follow, { now: String(resolved?.multi_query_count ?? "") })}`}
						error={err("rewrites")}
					>
						<Input
							{...props("rewrites", "rewrites")}
							inputMode="numeric"
							placeholder={follow(String(resolved?.multi_query_count ?? ""))}
							value={form.rewrites}
							onChange={(e) => set({ rewrites: e.target.value })}
						/>
					</Field>
				</div>
				<div className="grid gap-3 sm:grid-cols-2">
					<Field id={i("hyde")} label={t.hyde} hint={t.hydeHelp}>
						<NativeSelect
							id={i("hyde")}
							aria-describedby={`${i("hyde")}-hint`}
							value={form.hyde}
							onChange={(e) => set({ hyde: e.target.value as ProfileForm["hyde"] })}
						>
							<option value="">{`${t.triFollow} (${fill(t.followNow, { now: nowTri(resolved?.hyde_enabled) })})`}</option>
							<option value="on">{t.triOn}</option>
							<option value="off">{t.triOff}</option>
						</NativeSelect>
					</Field>
					<Field id={i("classify")} label={t.classify} hint={t.classifyHelp}>
						<NativeSelect
							id={i("classify")}
							aria-describedby={`${i("classify")}-hint`}
							value={form.classify}
							onChange={(e) => set({ classify: e.target.value as ProfileForm["classify"] })}
						>
							<option value="">{`${t.triFollow} (${fill(t.followNow, { now: nowTri(resolved?.query_classification_enabled) })})`}</option>
							<option value="on">{t.triOn}</option>
							<option value="off">{t.triOff}</option>
						</NativeSelect>
					</Field>
				</div>
			</Section>

			<Section title={t.sections.length} lead={t.sectionLeads.length}>
				<Field
					id={i("max")}
					label={t.maxAnswer}
					hint={`${t.maxAnswerHelp} ${fill(t.follow, { now: String(resolved?.max_answer_tokens ?? "") })}`}
					error={err("maxAnswer")}
				>
					<Input
						{...props("max", "maxAnswer")}
						inputMode="numeric"
						placeholder={follow(String(resolved?.max_answer_tokens ?? ""))}
						value={form.maxAnswer}
						onChange={(e) => set({ maxAnswer: e.target.value })}
					/>
				</Field>
				<div className="grid gap-1">
					<div className="flex items-center gap-2.5">
						<Checkbox
							id={i("followups")}
							checked={form.followups}
							onCheckedChange={(c) => set({ followups: c === true })}
							aria-describedby={`${i("followups")}-hint`}
						/>
						<label htmlFor={i("followups")} className="text-[13px] font-semibold">
							{t.followups}
						</label>
					</div>
					<small id={`${i("followups")}-hint`} className="ml-6 text-[12.5px] text-muted-foreground">
						{t.followupsHelp}
					</small>
				</div>
			</Section>

			<Section title={t.sections.speed} lead={t.sectionLeads.speed}>
				<div className="grid gap-3 sm:grid-cols-2">
					<Field id={i("priority")} label={t.priority} hint={t.priorityHelp}>
						<NativeSelect
							id={i("priority")}
							aria-describedby={`${i("priority")}-hint`}
							value={form.priority}
							onChange={(e) => set({ priority: e.target.value as ProfileForm["priority"] })}
						>
							{Object.entries(t.priorityOptions).map(([v, label]) => (
								<option key={v} value={v}>
									{label}
								</option>
							))}
						</NativeSelect>
					</Field>
					<Field id={i("conc")} label={t.concurrency} hint={t.concurrencyHelp} error={err("concurrency")}>
						<Input
							{...props("conc", "concurrency")}
							inputMode="numeric"
							placeholder="No limit"
							value={form.concurrency}
							onChange={(e) => set({ concurrency: e.target.value })}
						/>
					</Field>
				</div>
				<div className="grid gap-1">
					<div className="flex items-center gap-2.5">
						<Checkbox
							id={i("overrides")}
							checked={form.overrides}
							disabled={local}
							onCheckedChange={(c) => set({ overrides: c === true })}
							aria-describedby={`${i("overrides")}-hint`}
						/>
						<label htmlFor={i("overrides")} className="text-[13px] font-semibold">
							{t.overrides}
						</label>
					</div>
					<small id={`${i("overrides")}-hint`} className="ml-6 text-[12.5px] text-muted-foreground">
						{local ? t.localLocked : t.overridesHelp}
					</small>
					{err("overrides") ? (
						<small className="ml-6 text-[12.5px] font-medium text-destructive">{err("overrides")}</small>
					) : null}
				</div>
			</Section>
		</div>
	);
}
