import { useFieldArray, useFormContext, useWatch } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/lib/markdown";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { CorpusDetail } from "../../kbs/api";
import { Panel, SettingsGrid } from "../../kbs/ui";
import { ChipsControl, ChoiceControl, StringListControl, TextControl } from "../controls";
import { fillPersona } from "../defaults";
import { LocalizedTextField } from "../LocalizedTextField";
import { SectionShell } from "../SectionShell";
import { type FullConfig, voiceSchema } from "../schemas";
import type { AssistantState } from "../types";
import { useDraftSection } from "../useDraftSection";

type VoiceForm = FullConfig["voice"];

const opts = <T extends string>(map: Record<T, string>) =>
	(Object.keys(map) as T[]).map((value) => ({ value, label: map[value] }));

function TermPairs() {
	const { control, register, formState } = useFormContext<VoiceForm>();
	const { fields, append, remove } = useFieldArray({ control, name: "preferred_terms" });
	const err = (i: number, k: "use" | "instead_of") => formState.errors.preferred_terms?.[i]?.[k]?.message;
	return (
		<fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
			<legend className="mb-1 p-0 text-[13px] font-semibold">{t.voice.terms}</legend>
			<small className="text-[12.5px] text-muted-foreground">{t.voice.termsHint}</small>
			{fields.map((f, i) => (
				<div key={f.id} className="flex flex-wrap items-start gap-2">
					<div className="grid gap-0.5">
						<input
							aria-label={fill(t.voice.termUseLabel, { n: i + 1 })}
							maxLength={60}
							placeholder={t.voice.termUse}
							className="h-[34px] rounded-sm border border-input bg-muted px-2 text-sm"
							{...register(`preferred_terms.${i}.use`)}
						/>
						{err(i, "use") ? <small className="text-[12px] text-destructive">{err(i, "use")}</small> : null}
					</div>
					<span className="self-center text-[12.5px] text-muted-foreground">{t.voice.termInstead}</span>
					<div className="grid gap-0.5">
						<input
							aria-label={fill(t.voice.termInsteadLabel, { n: i + 1 })}
							maxLength={60}
							className="h-[34px] rounded-sm border border-input bg-muted px-2 text-sm"
							{...register(`preferred_terms.${i}.instead_of`)}
						/>
						{err(i, "instead_of") ? (
							<small className="text-[12px] text-destructive">{err(i, "instead_of")}</small>
						) : null}
					</div>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						aria-label={fill(t.form.removeItem, { what: fill(t.voice.termUseLabel, { n: i + 1 }) })}
						onClick={() => remove(i)}
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
					disabled={fields.length >= 30}
					onClick={() => append({ use: "", instead_of: "" })}
				>
					{t.voice.addTerm}
				</Button>
			</div>
		</fieldset>
	);
}

/** A live sample of the greeting, in the form's current wording. */
function GreetingSample({ name, business }: { name: string; business: string }) {
	const { control } = useFormContext<VoiceForm>();
	const greeting = useWatch({ control, name: "greeting" });
	const text = fillPersona(greeting?.en ?? "", { assistant_name: name, business_name: business });
	return (
		<Panel title={t.voice.sampleTitle}>
			<div className="p-4 text-[14px]">
				<div className="max-w-[88%] rounded-[14px] rounded-bl-[4px] border border-border bg-secondary px-3.5 py-2.5">
					<Markdown text={text || "…"} />
				</div>
			</div>
		</Panel>
	);
}

/** Voice (design 4.2): tone, length, format rules, wording and the greeting. */
export function VoiceSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const section = useDraftSection(kb.id, state, "voice", voiceSchema);
	const identity = section.config.identity;
	return (
		<SectionShell section={section}>
			<SettingsGrid>
				<div className="grid gap-4">
					<Panel title={t.voice.title} sub={t.voice.sub}>
						<div className="grid gap-4 p-4">
							<ChoiceControl<VoiceForm, keyof typeof t.voice.tones>
								name="tone_preset"
								label={t.voice.tone}
								options={opts(t.voice.tones)}
							/>
							<TextControl
								name="custom_tone"
								label={t.voice.customTone}
								hint={t.voice.customToneHint}
								maxLength={300}
								counter
							/>
							<ChoiceControl<VoiceForm, keyof typeof t.voice.formalities>
								name="formality"
								label={t.voice.formality}
								options={opts(t.voice.formalities)}
							/>
							<ChoiceControl<VoiceForm, keyof typeof t.voice.pronouns>
								name="hindi_pronoun"
								label={t.voice.pronoun}
								options={opts(t.voice.pronouns)}
							/>
							<ChoiceControl<VoiceForm, keyof typeof t.voice.lengths>
								name="response_length"
								label={t.voice.length}
								options={opts(t.voice.lengths)}
							/>
							<ChoiceControl<VoiceForm, keyof typeof t.voice.emojis>
								name="emoji"
								label={t.voice.emoji}
								options={opts(t.voice.emojis)}
							/>
						</div>
					</Panel>
					<Panel title={t.voice.formatTitle}>
						<div className="grid gap-4 p-4">
							<ChoiceControl<VoiceForm, keyof typeof t.voice.listsOptions>
								name="format.lists"
								label={t.voice.lists}
								options={opts(t.voice.listsOptions)}
							/>
							<ChoiceControl<VoiceForm, keyof typeof t.voice.tablesOptions>
								name="format.tables"
								label={t.voice.tables}
								options={opts(t.voice.tablesOptions)}
							/>
							<ChoiceControl<VoiceForm, keyof typeof t.voice.headingsOptions>
								name="format.headings"
								label={t.voice.headings}
								options={opts(t.voice.headingsOptions)}
							/>
							<TextControl
								name="format.custom"
								label={t.voice.formatCustom}
								hint={t.voice.formatCustomHint}
								maxLength={200}
								counter
							/>
						</div>
					</Panel>
					<Panel title={t.voice.greeting}>
						<div className="grid gap-4 p-4">
							<LocalizedTextField
								name="greeting"
								label={t.voice.greeting}
								max={300}
								hint={t.voice.greetingHint}
								languages={section.config.language.supported}
							/>
							<LocalizedTextField
								name="closing_line"
								label={t.voice.closing}
								max={200}
								rows={2}
								languages={section.config.language.supported}
							/>
						</div>
					</Panel>
					<Panel title={t.voice.terms}>
						<div className="grid gap-4 p-4">
							<TermPairs />
							<ChipsControl
								name="banned_phrases"
								label={t.voice.banned}
								hint={t.voice.bannedHint}
								max={30}
								maxLength={80}
								addLabel={t.voice.bannedAdd}
							/>
							<StringListControl
								name="voice_samples"
								label={t.voice.samples}
								hint={t.voice.samplesHint}
								max={5}
								maxLength={500}
								itemLabel={t.voice.sampleLabel}
								addLabel={t.voice.addSample}
								rows={3}
							/>
						</div>
					</Panel>
				</div>
				<GreetingSample name={identity.assistant_name} business={identity.business_name} />
			</SettingsGrid>
		</SectionShell>
	);
}
