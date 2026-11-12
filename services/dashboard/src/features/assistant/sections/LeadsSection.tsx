import { useFieldArray, useFormContext, useWatch } from "react-hook-form";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { CorpusDetail } from "../../kbs/api";
import { KbErrorAlert } from "../../kbs/KbErrorAlert";
import { Panel } from "../../kbs/ui";
import { noticeIsRecommended, RECOMMENDED_NOTICE } from "../consentNotice"; // TV2 reference
import {
	ChipsControl,
	ChoiceControl,
	NumberControl,
	OptionalNumberControl,
	SelectControl,
	SwitchControl,
	TextControl,
} from "../controls";
import { fillPersona, slugFor } from "../defaults";
import { useAssistantDirty } from "../dirty";
import { LocalizedTextField } from "../LocalizedTextField";
import { SectionShell } from "../SectionShell";
import { type FullConfig, leadsSchema } from "../schemas";
import type { AssistantState } from "../types";
import { useDraftSection } from "../useDraftSection";
import { NotifyFields } from "./NotifyFields";
import { QualificationRules } from "./QualificationRules";

type LeadsForm = FullConfig["leads"];
type Langs = FullConfig["language"]["supported"];

const opts = <T extends string>(map: Record<T, string>) =>
	(Object.keys(map) as T[]).map((value) => ({ value, label: map[value] }));

function TopicPicker({ topics }: { topics: { id: string; name: string }[] }) {
	const { control, setValue } = useFormContext<LeadsForm>();
	const chosen = useWatch({ control, name: "triggers.topics" }) ?? [];
	return (
		<fieldset className="m-0 grid min-w-0 gap-1.5 border-0 p-0">
			<legend className="mb-1 p-0 text-[13px] font-semibold">{t.leads.topicsLabel}</legend>
			{topics.length === 0 ? <small className="text-[12.5px] text-muted-foreground">{t.leads.topicsNone}</small> : null}
			{topics.map((x) => (
				<div key={x.id} className="flex items-center gap-2">
					<Checkbox
						id={`lead-topic-${x.id}`}
						checked={chosen.includes(x.id)}
						onCheckedChange={(v) =>
							setValue("triggers.topics", v === true ? [...chosen, x.id] : chosen.filter((c) => c !== x.id), {
								shouldDirty: true,
							})
						}
					/>
					<label htmlFor={`lead-topic-${x.id}`} className="text-[13.5px]">
						{x.name || x.id}
					</label>
				</div>
			))}
		</fieldset>
	);
}

function CustomFields({ languages }: { languages: Langs }) {
	const { control, getValues } = useFormContext<LeadsForm>();
	const { fields, append, remove } = useFieldArray({ control, name: "custom_fields" });
	const items = useWatch({ control, name: "custom_fields" }) ?? [];
	return (
		<div className="grid gap-3">
			{fields.map((f, i) => {
				const n = i + 1;
				return (
					<div key={f.id} className="grid gap-3 rounded-lg border border-border p-3">
						<LocalizedTextField
							name={`custom_fields.${i}.label`}
							label={fill(t.leads.customLabel, { n })}
							max={60}
							rows={1}
							languages={languages}
						/>
						<div className="grid gap-3 min-[700px]:grid-cols-3">
							<SelectControl
								name={`custom_fields.${i}.type`}
								label={fill(t.leads.customType, { n })}
								options={opts(t.leads.customTypes)}
							/>
							<NumberControl
								name={`custom_fields.${i}.max_length`}
								label={fill(t.leads.customMax, { n })}
								min={1}
								max={200}
							/>
							<SwitchControl name={`custom_fields.${i}.required`} label={fill(t.leads.customRequired, { n })} />
						</div>
						{items[i]?.type === "select" ? (
							<ChipsControl
								name={`custom_fields.${i}.options`}
								label={fill(t.leads.customOptions, { n })}
								max={20}
								maxLength={60}
								addLabel={t.leads.customOptionsAdd}
							/>
						) : null}
						<div>
							<Button
								type="button"
								variant="ghost"
								size="sm"
								aria-label={fill(t.leads.removeCustom, { n })}
								onClick={() => remove(i)}
							>
								{t.form.remove}
							</Button>
						</div>
					</div>
				);
			})}
			<div>
				<Button
					type="button"
					variant="outline"
					disabled={fields.length >= 6}
					onClick={() =>
						append({
							id: slugFor(
								`question-${fields.length + 1}`,
								getValues("custom_fields").map((x) => x.id),
							),
							label: { en: "" },
							type: "text",
							required: false,
							options: [],
							max_length: 120,
						})
					}
				>
					{t.leads.addCustom}
				</Button>
			</div>
		</div>
	);
}

function NoticePreview({ business }: { business: string }) {
	const { control } = useFormContext<LeadsForm>();
	const notice = useWatch({ control, name: "consent.notice" });
	const days = useWatch({ control, name: "consent.retention_days" });
	const text = fillPersona(notice?.en ?? "", { assistant_name: "", business_name: business }).replaceAll(
		"{retention_days}",
		String(days),
	);
	return text ? (
		<small className="text-[12.5px] text-muted-foreground">{fill(t.leads.consentPreview, { text })}</small>
	) : null;
}

// TV2 reference
/** Offers the recommended consent wording when the owner's text differs from it (never replaces
 * the owner's words on its own). */
export function RecommendedNotice() {
	const { control, setValue } = useFormContext<LeadsForm>();
	const notice = useWatch({ control, name: "consent.notice" }) ?? {};
	if (noticeIsRecommended(notice)) return null;
	return (
		<div className="grid gap-2 rounded-md border border-border bg-muted/40 p-3" data-testid="recommended-notice">
			<small className="text-[12.5px]">{t.leads.recommendedNotice}</small>
			<div>
				<Button
					type="button"
					variant="outline"
					size="sm"
					onClick={() => setValue("consent.notice", { ...RECOMMENDED_NOTICE }, { shouldDirty: true })}
				>
					{t.leads.recommendedNoticeUse}
				</Button>
			</div>
		</div>
	);
}
// /TV2 reference

/** Leads (design 4.7): when to ask, what to ask, consent and who is told. */
export function LeadsSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const section = useDraftSection(kb.id, state, "leads", leadsSchema);
	const { publishError } = useAssistantDirty();
	const languages = section.config.language.supported;
	const topics = section.config.scope.topics.map((x) => ({ id: x.id, name: x.name }));
	const on = useWatch({ control: section.form.control, name: "enabled" });
	const version = useWatch({ control: section.form.control, name: "consent.notice_version" });
	return (
		<SectionShell
			section={section}
			notice={
				on && publishError?.code === "encryption_not_configured" ? <KbErrorAlert error={publishError} /> : undefined
			}
		>
			<Panel title={t.leads.title} sub={t.leads.sub}>
				<div className="grid gap-4 p-4">
					<SwitchControl name="enabled" label={t.leads.enabled} />
					<ChoiceControl<LeadsForm, keyof typeof t.leads.modes>
						name="mode"
						label={t.leads.mode}
						options={opts(t.leads.modes)}
					/>
				</div>
			</Panel>
			<Panel title={t.leads.whenTitle}>
				<div className="grid gap-4 p-4">
					<SwitchControl name="triggers.buying_intent" label={t.leads.buying} />
					<SwitchControl name="triggers.no_answer" label={t.leads.noAnswer} />
					<OptionalNumberControl
						name="triggers.after_n_messages"
						label={t.leads.afterN}
						numberLabel={t.leads.afterNNumber}
						min={1}
						max={50}
						defaultValue={5}
					/>
					<TopicPicker topics={topics} />
					<ChipsControl
						name="triggers.page_url_globs"
						label={t.leads.pages}
						hint={t.leads.pagesHint}
						max={10}
						maxLength={200}
						addLabel={t.leads.pagesAdd}
					/>
					<SwitchControl name="triggers.human_request" label={t.leads.humanRequest} />
					<SwitchControl name="triggers.out_of_hours" label={t.leads.outOfHours} />
				</div>
			</Panel>
			<Panel title={t.leads.fieldsTitle}>
				<div className="grid gap-4 p-4">
					<ChoiceControl<LeadsForm, keyof typeof t.leads.modesField>
						name="name"
						label={t.leads.name}
						options={opts(t.leads.modesField)}
					/>
					<ChoiceControl<LeadsForm, keyof typeof t.leads.modesField>
						name="phone"
						label={t.leads.phone}
						options={opts(t.leads.modesField)}
					/>
					<ChoiceControl<LeadsForm, keyof typeof t.leads.modesField>
						name="email"
						label={t.leads.email}
						options={opts(t.leads.modesField)}
					/>
					<h3 className="m-0 text-[13px] font-semibold">{t.leads.customTitle}</h3>
					<CustomFields languages={languages} />
				</div>
			</Panel>
			<Panel title={t.leads.consentTitle}>
				<div className="grid gap-4 p-4">
					<LocalizedTextField
						name="consent.notice"
						label={t.leads.consentNotice}
						max={1500}
						rows={4}
						hint={t.leads.consentHint}
						languages={languages}
					/>
					<RecommendedNotice /> {/* TV2 reference */}
					<NoticePreview business={section.config.identity.business_name} />
					<TextControl name="consent.purpose" label={t.leads.purpose} maxLength={300} />
					<div className="max-w-[260px]">
						<NumberControl
							name="consent.retention_days"
							label={t.leads.retention}
							hint={t.leads.retentionHint}
							min={30}
							max={3650}
						/>
					</div>
					<TextControl name="consent.privacy_policy_url" label={t.leads.privacy} type="url" inputMode="url" />
					<small className="text-[12.5px] text-muted-foreground">
						{fill(t.leads.noticeVersion, { n: version })}. {t.leads.noticeVersionHint}
					</small>
				</div>
			</Panel>
			<Panel title={t.leads.qualTitle}>
				<div className="p-4">
					<QualificationRules />
				</div>
			</Panel>
			<Panel title={t.leads.notifyTitle}>
				<div className="p-4">
					<NotifyFields />
				</div>
			</Panel>
			<Panel title={t.leads.confirmTitle}>
				<div className="p-4">
					<LocalizedTextField
						name="confirmation_message"
						label={t.leads.confirm}
						max={300}
						rows={2}
						languages={languages}
					/>
				</div>
			</Panel>
		</SectionShell>
	);
}
