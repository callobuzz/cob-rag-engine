import { useEffect, useId, useRef } from "react";
import { useFieldArray, useFormContext, useWatch } from "react-hook-form";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import type { CorpusDetail } from "../../kbs/api";
import { Panel } from "../../kbs/ui";
import {
	ChoiceControl,
	InlineInput,
	NumberControl,
	SelectControl,
	StringListControl,
	TextControl,
	useFieldError,
} from "../controls";
import { isSampleDisclaimer, PROFILE_DISCLAIMERS, slugFor } from "../defaults";
import { LocalizedTextField } from "../LocalizedTextField";
import { SectionShell } from "../SectionShell";
import { type FullConfig, scopeSchema } from "../schemas";
import type { AssistantState } from "../types";
import { useDraftSection } from "../useDraftSection";

type ScopeForm = FullConfig["scope"];
type Langs = FullConfig["language"]["supported"];

const opts = <T extends string>(map: Record<T, string>) =>
	(Object.keys(map) as T[]).map((value) => ({ value, label: map[value] }));

const MODES = ["strict_docs", "docs_plus_domain", "open_with_guardrails"] as const;

function ModeCards() {
	const { control, setValue } = useFormContext<ScopeForm>();
	const mode = useWatch({ control, name: "scope_mode" });
	return (
		<div role="radiogroup" aria-label={t.scope.modeGroup} className="grid gap-2 min-[900px]:grid-cols-3">
			{MODES.map((m) => {
				const on = m === mode;
				return (
					// biome-ignore lint/a11y/useSemanticElements: a card with radio semantics
					<button
						key={m}
						type="button"
						role="radio"
						aria-checked={on}
						tabIndex={on ? 0 : -1}
						onClick={() => setValue("scope_mode", m, { shouldDirty: true })}
						className={cn(
							"grid content-start gap-1 rounded-lg border p-3 text-left",
							on ? "border-primary bg-primary-soft" : "border-border bg-card hover:bg-accent",
						)}
					>
						<span className="text-[14px] font-semibold">
							{t.scope.modes[m].title}
							{m === "docs_plus_domain" ? (
								<span className="ml-2 rounded-full bg-secondary px-2 py-0.5 text-[11px] font-semibold text-foreground-2">
									{t.scope.recommended}
								</span>
							) : null}
						</span>
						<span className="text-[12.5px] text-muted-foreground">{t.scope.modes[m].body}</span>
					</button>
				);
			})}
		</div>
	);
}

function QuickReplyButtons({ index }: { index: number }) {
	const { control } = useFormContext<ScopeForm>();
	const { fields, append, remove } = useFieldArray({ control, name: `topics.${index}.quick_replies` });
	return (
		<fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
			<legend className="mb-1 p-0 text-[13px] font-semibold">{fill(t.scope.topicButtons, { n: index + 1 })}</legend>
			{fields.map((f, k) => (
				<div key={f.id} className="flex flex-wrap items-start gap-2">
					<InlineInput
						name={`topics.${index}.quick_replies.${k}.label`}
						label={fill(t.scope.buttonLabel, { k: k + 1, n: index + 1 })}
						maxLength={20}
						className="w-[160px]"
					/>
					<InlineInput
						name={`topics.${index}.quick_replies.${k}.message`}
						label={fill(t.scope.buttonMessage, { k: k + 1, n: index + 1 })}
						maxLength={200}
						className="min-w-[200px] flex-1"
					/>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						aria-label={fill(t.scope.removeButton, { k: k + 1, n: index + 1 })}
						onClick={() => remove(k)}
					>
						{t.form.remove}
					</Button>
				</div>
			))}
			<div>
				<Button
					type="button"
					variant="outline"
					size="sm"
					disabled={fields.length >= 4}
					onClick={() => append({ label: "", message: "" })}
				>
					{t.scope.addButton}
				</Button>
			</div>
		</fieldset>
	);
}

function TopicCard({ index, onRemove, languages }: { index: number; onRemove: () => void; languages: Langs }) {
	const id = useId();
	const { control, register, setValue, getValues } = useFormContext<ScopeForm>();
	const name = useWatch({ control, name: `topics.${index}.name` });
	const verbatim = useWatch({ control, name: `topics.${index}.verbatim_answer` });
	const label = name?.trim() || t.scope.untitled;
	const nameReg = register(`topics.${index}.name`);
	return (
		<li className="grid gap-3 rounded-lg border border-border p-3">
			<div className="flex flex-wrap items-end gap-2">
				<div className="grid min-w-[220px] flex-1 gap-1">
					<label htmlFor={`${id}-name`} className="text-[13px] font-semibold">
						{fill(t.scope.topicName, { n: index + 1 })}
					</label>
					<Input
						id={`${id}-name`}
						maxLength={60}
						{...nameReg}
						onBlur={(e) => {
							void nameReg.onBlur(e);
							// A new topic gets a readable id from its first name; ids that others use never change.
							const current = getValues(`topics.${index}.id`);
							if (/^topic-\d+$/.test(current) && e.target.value.trim()) {
								const taken = getValues("topics")
									.map((x) => x.id)
									.concat(getValues("blocked_topics").map((x) => x.id))
									.filter((x) => x !== current);
								setValue(`topics.${index}.id`, slugFor(e.target.value, taken, current), { shouldDirty: true });
							}
						}}
					/>
				</div>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					aria-label={fill(t.scope.removeTopic, { n: index + 1 })}
					onClick={onRemove}
				>
					{t.form.remove}
				</Button>
			</div>
			<TextControl
				name={`topics.${index}.description`}
				label={fill(t.scope.topicDescription, { n: index + 1 })}
				maxLength={300}
				counter
			/>
			<StringListControl
				name={`topics.${index}.examples`}
				label={fill(t.scope.topicExamples, { n: index + 1 })}
				max={10}
				maxLength={200}
				itemLabel={fill(t.scope.topicExample, { t: index + 1 })}
				addLabel={t.scope.addExample}
				rows={1}
			/>
			<div className="grid gap-1">
				<div className="flex items-center gap-2">
					<Checkbox
						id={`${id}-exact`}
						checked={verbatim != null}
						onCheckedChange={(v) =>
							setValue(`topics.${index}.verbatim_answer`, v === true ? { en: "" } : null, {
								shouldDirty: true,
								shouldValidate: true,
							})
						}
					/>
					<label htmlFor={`${id}-exact`} className="text-[13.5px] font-semibold">
						{fill(t.scope.useExact, { name: label })}
					</label>
				</div>
				<small className="pl-6 text-[12.5px] text-muted-foreground">{t.scope.useExactHint}</small>
			</div>
			{verbatim != null ? (
				<LocalizedTextField
					name={`topics.${index}.verbatim_answer`}
					label={fill(t.scope.exactAnswer, { name: label })}
					max={2000}
					rows={4}
					languages={languages}
				/>
			) : null}
			<QuickReplyButtons index={index} />
		</li>
	);
}

function TopicsEditor({ languages }: { languages: Langs }) {
	const { control, getValues } = useFormContext<ScopeForm>();
	const { fields, append, remove } = useFieldArray({ control, name: "topics" });
	const error = useFieldError("topics");
	return (
		<div className="grid gap-3 p-4">
			{fields.length === 0 ? <p className="m-0 text-[13px] text-muted-foreground">{t.scope.topicsEmpty}</p> : null}
			<ul className="m-0 grid list-none gap-3 p-0">
				{fields.map((f, i) => (
					<TopicCard key={f.id} index={i} onRemove={() => remove(i)} languages={languages} />
				))}
			</ul>
			{error ? <small className="text-[12.5px] font-medium text-destructive">{error}</small> : null}
			<div>
				<Button
					type="button"
					variant="outline"
					disabled={fields.length >= 40}
					onClick={() => {
						const taken = getValues("topics")
							.map((x) => x.id)
							.concat(getValues("blocked_topics").map((x) => x.id));
						append({
							id: slugFor(`topic-${fields.length + 1}`, taken),
							name: "",
							description: "",
							examples: [],
							verbatim_answer: null,
							quick_replies: [],
						});
					}}
				>
					{t.scope.addTopic}
				</Button>
			</div>
		</div>
	);
}

function BlockedCard({ index, onRemove, languages }: { index: number; onRemove: () => void; languages: Langs }) {
	const { control, setValue } = useFormContext<ScopeForm>();
	const message = useWatch({ control, name: `blocked_topics.${index}.message` });
	const n = index + 1;
	return (
		<li className="grid gap-3 rounded-lg border border-border p-3">
			<div className="flex flex-wrap items-end gap-2">
				<div className="min-w-[220px] flex-1">
					<TextControl name={`blocked_topics.${index}.name`} label={fill(t.scope.blockedName, { n })} maxLength={60} />
				</div>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					aria-label={fill(t.scope.removeBlocked, { n })}
					onClick={onRemove}
				>
					{t.form.remove}
				</Button>
			</div>
			<TextControl
				name={`blocked_topics.${index}.description`}
				label={fill(t.scope.blockedDescription, { n })}
				maxLength={300}
				counter
			/>
			<SelectControl
				name={`blocked_topics.${index}.action`}
				label={fill(t.scope.blockedAction, { n })}
				options={opts(t.scope.blockedActions)}
			/>
			<div className="flex items-center gap-2">
				<Checkbox
					id={`blocked-msg-${index}`}
					checked={message != null}
					onCheckedChange={(v) =>
						setValue(`blocked_topics.${index}.message`, v === true ? { en: "" } : null, { shouldDirty: true })
					}
				/>
				<label htmlFor={`blocked-msg-${index}`} className="text-[13.5px]">
					{fill(t.scope.blockedMessageToggle, { n })}
				</label>
			</div>
			{message != null ? (
				<LocalizedTextField
					name={`blocked_topics.${index}.message`}
					label={fill(t.scope.blockedMessage, { n })}
					max={300}
					rows={2}
					languages={languages}
				/>
			) : null}
		</li>
	);
}

function BlockedEditor({ languages }: { languages: Langs }) {
	const { control, getValues } = useFormContext<ScopeForm>();
	const { fields, append, remove } = useFieldArray({ control, name: "blocked_topics" });
	return (
		<div className="grid gap-3 p-4">
			<ul className="m-0 grid list-none gap-3 p-0">
				{fields.map((f, i) => (
					<BlockedCard key={f.id} index={i} onRemove={() => remove(i)} languages={languages} />
				))}
			</ul>
			<div>
				<Button
					type="button"
					variant="outline"
					disabled={fields.length >= 40}
					onClick={() => {
						const taken = getValues("topics")
							.map((x) => x.id)
							.concat(getValues("blocked_topics").map((x) => x.id));
						append({
							id: slugFor(`blocked-${fields.length + 1}`, taken),
							name: "",
							description: "",
							action: "decline",
							message: null,
						});
					}}
				>
					{t.scope.addBlocked}
				</Button>
			</div>
		</div>
	);
}

/** Choosing a sensitive field fills the reminder text, unless the owner wrote their own. */
function SensitiveFields({ languages }: { languages: Langs }) {
	const { control, setValue, getValues } = useFormContext<ScopeForm>();
	const profile = useWatch({ control, name: "sensitive_profile" });
	const first = useRef(true);
	useEffect(() => {
		if (first.current) {
			first.current = false;
			return;
		}
		const current = getValues("disclaimers");
		if (!isSampleDisclaimer(current)) return;
		setValue("disclaimers", { ...(PROFILE_DISCLAIMERS[profile] ?? {}) }, { shouldDirty: true, shouldValidate: true });
	}, [profile, getValues, setValue]);
	return (
		<div className="grid gap-4 p-4">
			<SelectControl name="sensitive_profile" label={t.scope.sensitive} options={opts(t.scope.sensitiveOptions)} />
			{profile !== "none" ? (
				<LocalizedTextField
					name="disclaimers"
					label={t.scope.disclaimers}
					max={400}
					rows={3}
					hint={t.scope.disclaimersHint}
					languages={languages}
				/>
			) : null}
		</div>
	);
}

const ACTIONS = ["clarify", "handoff", "lead"] as const;

function FallbackActions() {
	const { control, setValue } = useFormContext<ScopeForm>();
	const actions = useWatch({ control, name: "fallback.actions" }) ?? [];
	return (
		<fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
			<legend className="mb-1 p-0 text-[13px] font-semibold">{t.scope.fallbackActions}</legend>
			{ACTIONS.map((a) => (
				<div key={a} className="flex items-center gap-2">
					<Checkbox
						id={`fallback-${a}`}
						checked={actions.includes(a)}
						onCheckedChange={(v) => {
							const next = v === true ? [...actions, a] : actions.filter((x) => x !== a);
							setValue(
								"fallback.actions",
								ACTIONS.filter((x) => next.includes(x)),
								{ shouldDirty: true },
							);
						}}
					/>
					<label htmlFor={`fallback-${a}`} className="text-[13.5px]">
						{t.scope.fallbackActionOptions[a]}
					</label>
				</div>
			))}
		</fieldset>
	);
}

function ConfidenceControl() {
	const { control, setValue } = useFormContext<ScopeForm>();
	const value = useWatch({ control, name: "min_retrieval_score" });
	const inputId = useId();
	const error = useFieldError("min_retrieval_score");
	const custom = value !== null && value !== undefined;
	const set = (on: boolean) =>
		setValue("min_retrieval_score", on ? 0.4 : null, { shouldDirty: true, shouldValidate: true });
	const radio = (on: boolean, label: string) => (
		// biome-ignore lint/a11y/useSemanticElements: a radio drawn as a row, kept in a radiogroup
		<button
			type="button"
			role="radio"
			aria-checked={custom === on}
			tabIndex={custom === on ? 0 : -1}
			onClick={() => set(on)}
			className={cn(
				"flex items-center gap-2 rounded-md border px-3 py-2 text-left text-[13.5px]",
				custom === on ? "border-primary bg-primary-soft" : "border-border bg-card hover:bg-accent",
			)}
		>
			{label}
		</button>
	);
	return (
		<div className="grid gap-3 p-4">
			<div role="radiogroup" aria-label={t.scope.confidenceGroup} className="flex flex-wrap gap-2">
				{radio(false, t.scope.confidenceDefault)}
				{radio(true, t.scope.confidenceCustom)}
			</div>
			{custom ? (
				<div className="max-w-[260px]">
					<Field id={inputId} label={t.scope.confidenceInput} hint={t.scope.confidenceHint} error={error}>
						<Input
							id={inputId}
							type="number"
							inputMode="decimal"
							min={0}
							max={1}
							step={0.01}
							aria-invalid={error ? true : undefined}
							aria-describedby={describedBy(inputId, { hint: true, error: !!error })}
							value={Number.isNaN(value) ? "" : String(value)}
							onChange={(e) =>
								setValue("min_retrieval_score", e.target.value === "" ? Number.NaN : Number(e.target.value), {
									shouldDirty: true,
									shouldValidate: true,
								})
							}
						/>
					</Field>
				</div>
			) : (
				<small className="text-[12.5px] text-muted-foreground">{t.scope.confidenceHint}</small>
			)}
		</div>
	);
}

/** Topics and limits (design 4.4): how strict, topics, blocked topics, fallback, fact check. */
export function ScopeSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const section = useDraftSection(kb.id, state, "scope", scopeSchema);
	const languages = section.config.language.supported;
	const claim = useWatch({ control: section.form.control, name: "claim_check" });
	return (
		<SectionShell section={section}>
			<Panel title={t.scope.modeTitle} sub={t.scope.modeSub}>
				<div className="p-4">
					<ModeCards />
				</div>
			</Panel>
			<Panel title={t.scope.topicsTitle} sub={t.scope.topicsSub}>
				<TopicsEditor languages={languages} />
			</Panel>
			<Panel title={t.scope.blockedTitle} sub={t.scope.blockedSub}>
				<BlockedEditor languages={languages} />
			</Panel>
			<Panel title={t.scope.sensitiveTitle}>
				<SensitiveFields languages={languages} />
			</Panel>
			<Panel title={t.scope.fallbackTitle}>
				<div className="grid gap-4 p-4">
					<LocalizedTextField
						name="fallback.no_answer_message"
						label={t.scope.noAnswer}
						max={400}
						rows={2}
						languages={languages}
					/>
					<FallbackActions />
					<LocalizedTextField
						name="fallback.softener"
						label={t.scope.softener}
						max={300}
						rows={2}
						hint={t.scope.softenerHint}
						languages={languages}
					/>
				</div>
			</Panel>
			<Panel title={t.scope.confidenceTitle}>
				<ConfidenceControl />
			</Panel>
			<Panel title={t.scope.factTitle}>
				<div className="grid gap-4 p-4">
					<ChoiceControl<ScopeForm, keyof typeof t.scope.factOptions>
						name="claim_check"
						label={t.scope.factCheck}
						options={opts(t.scope.factOptions)}
					/>
					{claim === "off" ? <FormAlert tone="warn">{t.scope.factOffWarning}</FormAlert> : null}
					<NumberControl
						name="max_off_topic_strikes"
						label={t.scope.strikes}
						hint={t.scope.strikesHint}
						min={1}
						max={10}
					/>
				</div>
			</Panel>
		</SectionShell>
	);
}
