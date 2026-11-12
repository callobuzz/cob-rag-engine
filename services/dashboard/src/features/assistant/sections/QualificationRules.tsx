import { useId, useState } from "react";
import { useFieldArray, useFormContext, useWatch } from "react-hook-form";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import { errorAt, SelectControl } from "../controls";
import type { FullConfig } from "../schemas";

type LeadsForm = FullConfig["leads"];
type Rule = LeadsForm["qualification"][number];
type Condition = Rule["conditions"][number];
type Op = Condition["op"];

const OPS = Object.keys(t.leads.qual.ops) as Op[];
const BASE_FIELDS = ["intent", "name", "phone", "email"] as const;

const NEW_CONDITION: Condition = { field: "intent", op: "eq", value: "buying_intent" };

/** What the person sees in the value box: a list reads as "a, b". */
function shown(value: Condition["value"] | undefined): string {
	if (Array.isArray(value)) return value.join(", ");
	return value == null ? "" : String(value);
}

/** What the box means for an operator: a list for "is one of", nothing for "is given". */
function stored(op: Op, text: string): Condition["value"] {
	if (op === "present") return null;
	if (op === "in")
		return text
			.split(",")
			.map((x) => x.trim())
			.filter(Boolean);
	return text;
}

function ConditionRow({ rule, index }: { rule: number; index: number }) {
	const base = `qualification.${rule}.conditions.${index}`;
	const id = useId();
	const { control, setValue, formState } = useFormContext<LeadsForm>();
	const cond = useWatch({ control, name: `qualification.${rule}.conditions.${index}` as never }) as
		| Condition
		| undefined;
	const custom = useWatch({ control, name: "custom_fields" }) ?? [];
	// What the person typed, kept as typed: a list is parsed on every key, and "a," must stay "a,".
	const [text, setText] = useState(() => shown(cond?.value));
	if (!cond) return null;
	const own = custom.map((f) => ({ value: f.id, label: f.label?.en || f.id }));
	const known = new Set<string>([...BASE_FIELDS, ...own.map((o) => o.value)]);
	const fields = [
		...BASE_FIELDS.map((value) => ({ value: value as string, label: t.leads.qual.fields[value] })),
		...own,
		...(known.has(cond.field) ? [] : [{ value: cond.field, label: cond.field }]),
	];
	const valueError = errorAt(formState.errors, `${base}.value`);
	const needsValue = cond.op !== "present";
	const intentPick = cond.field === "intent" && (cond.op === "eq" || cond.op === "neq");
	const set = (name: string, v: unknown) =>
		setValue(`${base}.${name}` as never, v as never, { shouldDirty: true, shouldValidate: formState.isSubmitted });
	return (
		<div className="grid gap-3 min-[700px]:grid-cols-3">
			<SelectControl name={`${base}.field`} label={fill(t.leads.qual.field, { n: index + 1 })} options={fields} />
			<Field id={`${id}-op`} label={fill(t.leads.qual.op, { n: index + 1 })}>
				<NativeSelect
					id={`${id}-op`}
					value={cond.op}
					onChange={(e) => {
						const op = e.target.value as Op;
						setValue(`${base}.op` as never, op as never, { shouldDirty: true });
						set("value", stored(op, text));
					}}
				>
					{OPS.map((op) => (
						<option key={op} value={op}>
							{t.leads.qual.ops[op]}
						</option>
					))}
				</NativeSelect>
			</Field>
			{needsValue ? (
				<Field
					id={`${id}-value`}
					label={fill(t.leads.qual.value, { n: index + 1 })}
					hint={cond.op === "in" ? t.leads.qual.listHint : undefined}
					error={valueError}
				>
					{intentPick ? (
						<NativeSelect
							id={`${id}-value`}
							value={shown(cond.value)}
							aria-invalid={valueError ? true : undefined}
							aria-describedby={describedBy(`${id}-value`, { error: !!valueError })}
							onChange={(e) => {
								setText(e.target.value);
								set("value", e.target.value);
							}}
						>
							{t.leads.qual.reasons[shown(cond.value) as keyof typeof t.leads.qual.reasons] ? null : (
								<option value="">{t.leads.qual.choose}</option>
							)}
							{Object.entries(t.leads.qual.reasons).map(([value, label]) => (
								<option key={value} value={value}>
									{label}
								</option>
							))}
						</NativeSelect>
					) : (
						<Input
							id={`${id}-value`}
							value={text}
							maxLength={200}
							aria-invalid={valueError ? true : undefined}
							aria-describedby={describedBy(`${id}-value`, {
								hint: cond.op === "in",
								error: !!valueError,
							})}
							onChange={(e) => {
								setText(e.target.value);
								set("value", stored(cond.op, e.target.value));
							}}
						/>
					)}
				</Field>
			) : null}
		</div>
	);
}

function RuleBox({ index, onRemove }: { index: number; onRemove: () => void }) {
	const { control, formState } = useFormContext<LeadsForm>();
	const name = `qualification.${index}.conditions` as const;
	const { fields, append, remove } = useFieldArray({ control, name: name as never });
	const n = index + 1;
	const listError = errorAt(formState.errors, `${name}`) ?? errorAt(formState.errors, `${name}.root`);
	return (
		<fieldset className="m-0 grid min-w-0 gap-3 rounded-lg border border-border p-3">
			<legend className="px-1 text-[13px] font-semibold">{fill(t.leads.qual.rule, { n })}</legend>
			<div className="grid gap-3 min-[700px]:grid-cols-2">
				<SelectControl
					name={`qualification.${index}.status`}
					label={fill(t.leads.qual.status, { n })}
					options={[
						{ value: "qualified", label: t.leads.qual.statuses.qualified },
						{ value: "partial", label: t.leads.qual.statuses.partial },
					]}
				/>
			</div>
			<p className="m-0 text-[12.5px] text-muted-foreground">{t.leads.qual.when}</p>
			{fields.map((f, i) => (
				<div key={f.id} className="grid gap-2">
					<ConditionRow rule={index} index={i} />
					{fields.length > 1 ? (
						<div>
							<Button
								type="button"
								variant="ghost"
								size="sm"
								aria-label={fill(t.leads.qual.removeCondition, { n: i + 1, rule: n })}
								onClick={() => remove(i)}
							>
								{t.form.remove}
							</Button>
						</div>
					) : null}
				</div>
			))}
			{listError ? <small className="text-[12.5px] font-medium text-destructive">{listError}</small> : null}
			<div className="flex flex-wrap gap-2">
				<Button
					type="button"
					variant="outline"
					size="sm"
					disabled={fields.length >= 5}
					onClick={() => append({ ...NEW_CONDITION } as never)}
				>
					{fill(t.leads.qual.addCondition, { rule: n })}
				</Button>
				<Button
					type="button"
					variant="ghost"
					size="sm"
					aria-label={fill(t.leads.qual.removeRule, { n })}
					onClick={onRemove}
				>
					{t.form.remove}
				</Button>
			</div>
		</fieldset>
	);
}

/**
 * Qualification rules (design 4.7): a lead is qualified when every condition of any qualified rule
 * holds, else partly qualified when every condition of a partly-qualified rule holds, else not
 * qualified. No rules, no mark.
 */
export function QualificationRules() {
	const { control } = useFormContext<LeadsForm>();
	const { fields, append, remove } = useFieldArray({ control, name: "qualification" });
	return (
		<div className="grid gap-3">
			<small className="text-[12.5px] text-muted-foreground">{t.leads.qual.sub}</small>
			{fields.length === 0 ? <p className="m-0 text-[13px] text-muted-foreground">{t.leads.qual.empty}</p> : null}
			{fields.map((f, i) => (
				<RuleBox key={f.id} index={i} onRemove={() => remove(i)} />
			))}
			<div>
				<Button
					type="button"
					variant="outline"
					disabled={fields.length >= 10}
					onClick={() => append({ status: "qualified", conditions: [{ ...NEW_CONDITION }] })}
				>
					{t.leads.qual.add}
				</Button>
			</div>
		</div>
	);
}
