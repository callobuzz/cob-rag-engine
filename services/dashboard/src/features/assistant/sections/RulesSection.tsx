import { Wand2 } from "lucide-react";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import { useFieldArray, useFormContext, useWatch } from "react-hook-form";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { UsageMeter } from "@/components/patterns/UsageMeter";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { CorpusDetail } from "../../kbs/api";
import { Panel } from "../../kbs/ui";
import { errorAt, NullableSelectControl, NumberControl, SelectControl, TextControl } from "../controls";
import { slugFor } from "../defaults";
import { SectionShell } from "../SectionShell";
import { FALLBACK_RULE_LIMIT, type FullConfig, LANG_KEYS, RULE_TYPES, rulesSchemaFor, TURN_LABELS } from "../schemas";
import type { AssistantState } from "../types";
import { useDraftSection } from "../useDraftSection";
import { ImproveRuleDialog } from "./ImproveRuleDialog";
import { useRuleBudget } from "./useRuleBudget";

type Rule = FullConfig["rules"][number];
type RulesForm = { rules: Rule[] };

const EMPTY_WHEN = {
	topic: null,
	label: null,
	language: null,
	page_url_glob: null,
	office_hours: null,
	lead_status: null,
};

/** A rule whose conditions are all empty applies always: store that as no condition at all. */
function whenOrNull(when: Rule["when"] | undefined): Rule["when"] {
	if (!when) return null;
	const full = { ...EMPTY_WHEN, ...when };
	const blank = (v: unknown) => v === null || v === undefined || v === "";
	return Object.values(full).every(blank)
		? null
		: {
				topic: blank(full.topic) ? null : full.topic,
				label: blank(full.label) ? null : full.label,
				language: blank(full.language) ? null : full.language,
				page_url_glob: blank(full.page_url_glob) ? null : full.page_url_glob,
				office_hours: blank(full.office_hours) ? null : full.office_hours,
				lead_status: blank(full.lead_status) ? null : full.lead_status,
			};
}

const MAP = {
	toForm: (rules: Rule[]): RulesForm => ({ rules }),
	fromForm: (v: RulesForm): Rule[] => v.rules.map((r) => ({ ...r, when: whenOrNull(r.when) })),
};

const typeOptions = RULE_TYPES.map((v) => ({ value: v as string, label: t.rules.types[v] }));

function Chips({ rule, topics }: { rule: Rule; topics: { id: string; name: string }[] }) {
	const chips: string[] = [];
	const w = rule.when;
	if (w?.topic) chips.push(fill(t.rules.whenTopic, { v: topics.find((x) => x.id === w.topic)?.name ?? w.topic }));
	if (w?.label) chips.push(t.rules.turnTypes[w.label]);
	if (w?.language) chips.push(t.langs[w.language]);
	if (w?.office_hours) chips.push(fill(t.rules.whenOffice, { v: t.rules.offices[w.office_hours] }));
	if (w?.page_url_glob) chips.push(fill(t.rules.whenPage, { v: w.page_url_glob }));
	if (w?.lead_status) chips.push(fill(t.rules.whenLead, { v: t.rules.leadStatuses[w.lead_status].toLowerCase() }));
	return (
		<span className="flex flex-wrap gap-1.5">
			{(chips.length ? chips : [t.rules.always]).map((c) => (
				<span key={c} className="rounded-full bg-secondary px-2 py-0.5 text-[11.5px] text-foreground-2">
					{c}
				</span>
			))}
			<span className="rounded-full bg-secondary px-2 py-0.5 text-[11.5px] text-foreground-2">
				{fill(t.rules.priorityChip, { n: rule.priority })}
			</span>
		</span>
	);
}

function RuleRow({
	kb,
	index,
	open,
	onToggle,
	onDelete,
	topics,
}: {
	kb: string;
	index: number;
	open: boolean;
	onToggle: () => void;
	onDelete: () => void;
	topics: { id: string; name: string }[];
}) {
	const id = useId();
	const { control, setValue, getValues, formState } = useFormContext<RulesForm>();
	const rule = useWatch({ control, name: `rules.${index}` });
	const n = index + 1;
	const [improving, setImproving] = useState(false);
	const dirty = formState.dirtyFields.rules?.[index];
	const edited = !!(dirty?.instruction || dirty?.type || dirty?.when || dirty?.priority);
	// An owner's edit of a template or setup rule makes it theirs (statistics and the setup flow read `source`).
	useEffect(() => {
		if (edited && getValues(`rules.${index}.source`) !== "owner") {
			setValue(`rules.${index}.source`, "owner", { shouldDirty: true });
		}
	}, [edited, getValues, index, setValue]);
	useEffect(() => {
		// The editor edits all six conditions; make them exist (empty) without marking the form dirty.
		if (open && !getValues(`rules.${index}.when`))
			setValue(`rules.${index}.when`, { ...EMPTY_WHEN }, { shouldDirty: false });
	}, [open, getValues, index, setValue]);
	if (!rule) return null;
	return (
		<li className="grid gap-2 rounded-lg border border-border p-3">
			<div className="flex flex-wrap items-center gap-3">
				<Switch
					aria-label={fill(t.rules.on, { n })}
					checked={rule.enabled}
					onCheckedChange={(v) => setValue(`rules.${index}.enabled`, v, { shouldDirty: true, shouldValidate: true })}
				/>
				<div className="grid min-w-0 flex-1 gap-1">
					<span className="break-words text-[13.5px]">{rule.instruction || "…"}</span>
					<Chips rule={rule} topics={topics} />
				</div>
				<Badge variant="default">{t.rules.sources[rule.source]}</Badge>
				<Button
					type="button"
					variant="outline"
					size="sm"
					aria-expanded={open}
					aria-controls={`${id}-editor`}
					onClick={onToggle}
				>
					{open ? fill(t.rules.done, { n }) : fill(t.rules.edit, { n })}
				</Button>
				<Button
					type="button"
					variant="outline"
					size="sm"
					aria-label={fill(t.rules.improve.button, { n })}
					disabled={!rule.instruction.trim()}
					onClick={() => setImproving(true)}
				>
					<Wand2 aria-hidden />
					{t.rules.improve.label}
				</Button>
				<Button type="button" variant="ghost" size="sm" aria-label={fill(t.rules.delete, { n })} onClick={onDelete}>
					{t.form.remove}
				</Button>
			</div>
			{open ? (
				<div id={`${id}-editor`} className="grid gap-3 border-t border-border pt-3">
					<SelectControl name={`rules.${index}.type`} label={t.rules.type} options={typeOptions} />
					<TextControl
						name={`rules.${index}.instruction`}
						label={t.rules.instruction}
						hint={t.rules.instructionHint}
						rows={2}
						maxLength={500}
						counter
					/>
					<fieldset className="m-0 grid min-w-0 gap-3 border-0 p-0">
						<legend className="mb-1 p-0 text-[13px] font-semibold">{t.rules.when}</legend>
						<small className="text-[12.5px] text-muted-foreground">{t.rules.whenHint}</small>
						<div className="grid gap-3 min-[700px]:grid-cols-2">
							<NullableSelectControl
								name={`rules.${index}.when.topic`}
								label={t.rules.topic}
								emptyLabel={t.rules.any}
								options={topics.map((x) => ({ value: x.id, label: x.name || x.id }))}
							/>
							<NullableSelectControl
								name={`rules.${index}.when.label`}
								label={t.rules.turnType}
								emptyLabel={t.rules.any}
								options={TURN_LABELS.map((v) => ({ value: v, label: t.rules.turnTypes[v] }))}
							/>
							<NullableSelectControl
								name={`rules.${index}.when.language`}
								label={t.rules.language}
								emptyLabel={t.rules.any}
								options={LANG_KEYS.map((v) => ({ value: v, label: t.langs[v] }))}
							/>
							<NullableSelectControl
								name={`rules.${index}.when.office_hours`}
								label={t.rules.office}
								emptyLabel={t.rules.any}
								options={(["open", "closed"] as const).map((v) => ({ value: v, label: t.rules.offices[v] }))}
							/>
							<NullableSelectControl
								name={`rules.${index}.when.lead_status`}
								label={t.rules.leadStatus}
								emptyLabel={t.rules.any}
								options={(["none", "captured"] as const).map((v) => ({ value: v, label: t.rules.leadStatuses[v] }))}
							/>
							<TextControl
								name={`rules.${index}.when.page_url_glob`}
								label={t.rules.page}
								hint={t.rules.pageHint}
								maxLength={200}
							/>
						</div>
					</fieldset>
					<div className="max-w-[240px]">
						<NumberControl
							name={`rules.${index}.priority`}
							label={t.rules.priority}
							hint={t.rules.priorityHint}
							min={0}
							max={100}
						/>
					</div>
				</div>
			) : null}
			{improving ? (
				<ImproveRuleDialog
					kb={kb}
					rule={{ id: rule.id, type: rule.type, instruction: rule.instruction }}
					onClose={() => setImproving(false)}
					onAccept={(text) => {
						setValue(`rules.${index}.instruction`, text, { shouldDirty: true, shouldValidate: true });
						setImproving(false);
					}}
				/>
			) : null}
		</li>
	);
}

function RulesBody({
	kb,
	topics,
	budget,
}: {
	kb: string;
	topics: { id: string; name: string }[];
	budget: ReturnType<typeof useRuleBudget>;
}) {
	const { control, getValues, formState } = useFormContext<RulesForm>();
	const { fields, append, remove } = useFieldArray({ control, name: "rules" });
	const seen = useRef(fields.length);
	const rules = useWatch({ control, name: "rules" }) ?? [];
	const [openId, setOpenId] = useState<string | null>(null);
	const [deleting, setDeleting] = useState<number | null>(null);
	// A rule that was just added opens its editor, so the owner can write it at once.
	useEffect(() => {
		if (fields.length > seen.current) {
			const last = fields[fields.length - 1];
			if (last) setOpenId(last.id);
		}
		seen.current = fields.length;
	}, [fields]);
	const enabledCount = rules.filter((r) => r.enabled).length;
	const over = budget.used !== null && budget.used > budget.budget;
	const listError = errorAt(formState.errors, "rules") ?? errorAt(formState.errors, "rules.root");
	return (
		<Panel title={t.rules.title} sub={t.rules.sub}>
			<div className="grid gap-3 p-4">
				<div className="grid gap-1 rounded-lg border border-border">
					<p className="m-0 border-b border-border px-4 py-3 text-[13.5px] font-semibold" aria-live="polite">
						{fill(t.rules.counter, { n: enabledCount, limit: budget.limit })}
					</p>
					{budget.used !== null ? (
						<UsageMeter label={t.rules.tokens} used={budget.used} limit={budget.budget} hint={t.rules.tokensHint} />
					) : (
						<p className="m-0 px-4 py-3 text-[13px] text-muted-foreground">
							{t.rules.tokens}: {budget.failed ? t.rules.cannotMeasure : t.rules.measuring}
						</p>
					)}
				</div>
				{over ? <FormAlert tone="warn">{t.rules.overBudget}</FormAlert> : null}
				{listError ? <FormAlert>{listError}</FormAlert> : null}
				{fields.length === 0 ? <p className="m-0 text-[13px] text-muted-foreground">{t.rules.empty}</p> : null}
				{RULE_TYPES.map((type) => {
					const rows = fields.map((f, i) => ({ f, i })).filter(({ i }) => rules[i]?.type === type);
					if (!rows.length) return null;
					return (
						<section key={type} aria-label={t.rules.types[type]} className="grid gap-2">
							<h3 className="m-0 text-[12.5px] font-semibold uppercase tracking-wide text-muted-foreground">
								{t.rules.types[type]}
							</h3>
							<ul className="m-0 grid list-none gap-2 p-0">
								{rows.map(({ f, i }) => (
									<RuleRow
										key={f.id}
										kb={kb}
										index={i}
										open={openId === f.id}
										topics={topics}
										onToggle={() => setOpenId(openId === f.id ? null : f.id)}
										onDelete={() => setDeleting(i)}
									/>
								))}
							</ul>
						</section>
					);
				})}
				<div>
					<Button
						type="button"
						variant="outline"
						disabled={fields.length >= 100}
						onClick={() => {
							const taken = getValues("rules").map((r) => r.id);
							append({
								id: slugFor(`rule-${fields.length + 1}`, taken),
								type: "communication",
								when: { ...EMPTY_WHEN },
								instruction: "",
								enabled: true,
								priority: 50,
								source: "owner",
							});
						}}
					>
						{t.rules.add}
					</Button>
				</div>
			</div>
			<ConfirmDialog
				open={deleting !== null}
				onOpenChange={(o) => {
					if (!o) setDeleting(null);
				}}
				title={t.rules.deleteTitle}
				body={t.rules.deleteBody}
				confirmLabel={t.rules.deleteConfirm}
				onConfirm={() => {
					if (deleting !== null) remove(deleting);
					setDeleting(null);
				}}
			/>
		</Panel>
	);
}

/** Guidance rules (design 4.5): short instructions with conditions, counted against the engine's limits. */
export function RulesSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const limitRef = useRef(FALLBACK_RULE_LIMIT);
	const schema = useMemo(() => rulesSchemaFor(() => limitRef.current), []);
	const section = useDraftSection<"rules", RulesForm>(kb.id, state, "rules", schema, MAP);
	const topics = section.config.scope.topics.map((x) => ({ id: x.id, name: x.name }));
	const watched = useWatch({ control: section.form.control, name: "rules" }) ?? [];
	const budget = useRuleBudget(kb.id, { ...section.config, rules: watched });
	limitRef.current = budget.limit;
	return (
		<SectionShell section={section}>
			<RulesBody kb={kb.id} topics={topics} budget={budget} />
		</SectionShell>
	);
}
