import { useFieldArray, useFormContext, useWatch } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { CorpusDetail } from "../../kbs/api";
import { Panel } from "../../kbs/ui";
import { InlineInput, NumberControl, SelectControl, SwitchControl, TextControl } from "../controls";
import { slugFor } from "../defaults";
import { LocalizedTextField } from "../LocalizedTextField";
import { SectionShell } from "../SectionShell";
import { CTA_TYPES, conversationSchema, type FullConfig } from "../schemas";
import type { AssistantState } from "../types";
import { useDraftSection } from "../useDraftSection";

type ConversationForm = FullConfig["conversation"];
type Langs = FullConfig["language"]["supported"];

function Starters() {
	const { control } = useFormContext<ConversationForm>();
	const { fields, append, remove } = useFieldArray({ control, name: "starters" });
	return (
		<div className="grid gap-3 p-4">
			{fields.map((f, i) => (
				<div key={f.id} className="flex flex-wrap items-start gap-2">
					<InlineInput
						name={`starters.${i}.label`}
						label={fill(t.conversation.starterText, { n: i + 1 })}
						maxLength={20}
						className="w-[170px]"
					/>
					<InlineInput
						name={`starters.${i}.message`}
						label={fill(t.conversation.starterSends, { n: i + 1 })}
						maxLength={200}
						className="min-w-[220px] flex-1"
					/>
					<Button
						type="button"
						variant="ghost"
						size="sm"
						aria-label={fill(t.conversation.removeStarter, { n: i + 1 })}
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
					disabled={fields.length >= 4}
					onClick={() => append({ label: "", message: "" })}
				>
					{t.conversation.addStarter}
				</Button>
			</div>
		</div>
	);
}

function OneCta() {
	const { control, setValue } = useFormContext<ConversationForm>();
	const max = useWatch({ control, name: "cta.max_per_answer" });
	return (
		<div className="grid gap-1">
			<div className="flex items-center gap-2.5">
				<Switch
					id="cta-one"
					checked={max === 1}
					onCheckedChange={(v) => setValue("cta.max_per_answer", v ? 1 : 0, { shouldDirty: true })}
				/>
				<label htmlFor="cta-one" className="text-[13.5px] font-semibold">
					{t.conversation.ctaOne}
				</label>
			</div>
			<small className="pl-[42px] text-[12.5px] text-muted-foreground">{t.conversation.ctaOneHint}</small>
		</div>
	);
}

function Greetings({ languages }: { languages: Langs }) {
	const { control, register, getValues, setValue } = useFormContext<ConversationForm>();
	const { fields, append, remove } = useFieldArray({ control, name: "proactive_greetings" });
	const items = useWatch({ control, name: "proactive_greetings" }) ?? [];
	return (
		<div className="grid gap-3 p-4">
			{fields.map((f, i) => {
				const n = i + 1;
				return (
					<div key={f.id} className="grid gap-3 rounded-lg border border-border p-3">
						<div className="flex flex-wrap items-center gap-3">
							<Switch
								aria-label={fill(t.conversation.greetingOn, { n })}
								checked={items[i]?.enabled ?? false}
								onCheckedChange={(v) => setValue(`proactive_greetings.${i}.enabled`, v, { shouldDirty: true })}
							/>
							<span className="flex-1 text-[13.5px] font-semibold">#{n}</span>
							<Button
								type="button"
								variant="ghost"
								size="sm"
								aria-label={fill(t.conversation.removeGreeting, { n })}
								onClick={() => remove(i)}
							>
								{t.form.remove}
							</Button>
						</div>
						<div className="grid gap-3 min-[700px]:grid-cols-2">
							<TextControl
								name={`proactive_greetings.${i}.page_url_glob`}
								label={fill(t.conversation.greetingPage, { n })}
								hint={t.conversation.greetingPageHint}
								maxLength={200}
							/>
							<NumberControl
								name={`proactive_greetings.${i}.delay_seconds`}
								label={fill(t.conversation.greetingDelay, { n })}
								min={0}
								max={300}
							/>
							<SelectControl
								name={`proactive_greetings.${i}.frequency`}
								label={fill(t.conversation.greetingFrequency, { n })}
								options={(Object.keys(t.conversation.frequencies) as (keyof typeof t.conversation.frequencies)[]).map(
									(v) => ({
										value: v,
										label: t.conversation.frequencies[v],
									}),
								)}
							/>
						</div>
						<LocalizedTextField
							name={`proactive_greetings.${i}.message`}
							label={fill(t.conversation.greetingMessage, { n })}
							max={200}
							rows={2}
							languages={languages}
						/>
						<input type="hidden" {...register(`proactive_greetings.${i}.id`)} />
					</div>
				);
			})}
			<div>
				<Button
					type="button"
					variant="outline"
					disabled={fields.length >= 10}
					onClick={() =>
						append({
							id: slugFor(
								`greeting-${fields.length + 1}`,
								getValues("proactive_greetings").map((g) => g.id),
							),
							page_url_glob: "",
							delay_seconds: 15,
							message: { en: "" },
							frequency: "once_per_session",
							enabled: true,
						})
					}
				>
					{t.conversation.addGreeting}
				</Button>
			</div>
		</div>
	);
}

/** Conversation (design 4.6): starters, follow-ups, the next-step policy and greetings on pages. */
export function ConversationSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const section = useDraftSection(kb.id, state, "conversation", conversationSchema);
	const languages = section.config.language.supported;
	const cta = useWatch({ control: section.form.control, name: "cta.buying_intent_cta" });
	return (
		<SectionShell section={section}>
			<Panel title={t.conversation.startersTitle} sub={t.conversation.startersSub}>
				<Starters />
			</Panel>
			<Panel title={t.conversation.ctaTitle}>
				<div className="grid gap-4 p-4">
					<SwitchControl name="ai_followups" label={t.conversation.followups} hint={t.conversation.followupsHint} />
					<OneCta />
					<SelectControl
						name="cta.buying_intent_cta"
						label={t.conversation.buyingCta}
						hint={t.conversation.buyingCtaHint}
						options={CTA_TYPES.map((v) => ({ value: v, label: t.conversation.ctaOptions[v] }))}
					/>
					{cta === "link" ? (
						<>
							<TextControl name="cta.link_url" label={t.conversation.linkUrl} type="url" inputMode="url" />
							<LocalizedTextField
								name="cta.link_label"
								label={t.conversation.linkLabel}
								max={40}
								rows={1}
								languages={languages}
							/>
						</>
					) : null}
				</div>
			</Panel>
			<Panel title={t.conversation.greetingsTitle} sub={t.conversation.greetingsSub}>
				<Greetings languages={languages} />
			</Panel>
		</SectionShell>
	);
}
