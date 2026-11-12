import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useId } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import { usePlanChoices } from "./api";
import { ConversationEditor } from "./ConversationEditor";
import { useDraft } from "./DraftProvider";
import type { LText } from "./draft";
import { fieldId } from "./draft";
import { LocalizedField } from "./LocalizedField";
import { type FieldSpec, LEAD_SPEC } from "./sections";

export type Rec = Record<string, unknown>;

/**
 * Draws a form from a list of field specs over a plain object. `path` is where the object sits in
 * the draft (`home.pain-wall`, the same the engine reports); `limitBase` is the same place in the
 * engine's `limits` (`pain_wall`).
 */
export function Fields({
	specs,
	value,
	onChange,
	path,
	limitBase,
}: {
	specs: FieldSpec[];
	value: Rec;
	onChange: (next: Rec) => void;
	path: string;
	limitBase: string;
}) {
	const set = (key: string, v: unknown) => onChange({ ...value, [key]: v });
	return (
		<div className="grid gap-4">
			{specs.map((spec) => (
				<Field
					key={spec.key}
					spec={spec}
					value={value[spec.key]}
					onChange={(v) => set(spec.key, v)}
					path={`${path}.${spec.key}`}
					limitBase={limitBase}
				/>
			))}
		</div>
	);
}

function Field({
	spec,
	value,
	onChange,
	path,
	limitBase,
}: {
	spec: FieldSpec;
	value: unknown;
	onChange: (v: unknown) => void;
	path: string;
	limitBase: string;
}) {
	switch (spec.k) {
		case "text":
			return (
				<LocalizedField
					path={path}
					label={spec.label}
					value={value as LText | null | undefined}
					onChange={onChange}
					limitKey={`${limitBase}.${spec.key}`}
					long={spec.long}
					required={spec.required}
					hint={spec.hint}
				/>
			);
		case "plain":
			return <PlainField spec={spec} value={value} onChange={onChange} path={path} />;
		case "number":
			return <NumberField spec={spec} value={value} onChange={onChange} path={path} />;
		case "switch":
			return <SwitchField spec={spec} value={value === true} onChange={onChange} path={path} />;
		case "select":
			return <SelectField spec={spec} value={String(value ?? "")} onChange={onChange} path={path} />;
		case "multi":
			return <MultiField spec={spec} value={(value as string[] | undefined) ?? []} onChange={onChange} />;
		case "asset":
			return <AssetField spec={spec} value={(value as string | undefined) ?? ""} onChange={onChange} path={path} />;
		case "plans":
			return <PlansField spec={spec} value={(value as string[] | undefined) ?? []} onChange={onChange} />;
		case "group":
			return (
				<fieldset className="grid gap-3 rounded-sm border border-border p-3">
					<legend className="px-1 text-[13px] font-semibold">{spec.label}</legend>
					<Fields
						specs={spec.fields}
						value={(value as Rec | undefined) ?? {}}
						onChange={onChange}
						path={path}
						limitBase={`${limitBase}.${spec.key}`}
					/>
				</fieldset>
			);
		case "list":
			return (
				<ListField
					spec={spec}
					value={(value as unknown[] | undefined) ?? []}
					onChange={onChange}
					path={path}
					limitBase={limitBase}
				/>
			);
		case "links":
			return (
				<LinksField
					spec={spec}
					value={(value as Rec[] | undefined) ?? []}
					onChange={onChange}
					path={path}
					limitBase={limitBase}
				/>
			);
		case "conversation":
			return <ConversationEditor value={value as Rec} onChange={onChange} path={path} limitBase={limitBase} />;
		case "lead":
			return (
				<LeadFields
					value={(value as Rec | null | undefined) ?? null}
					onChange={onChange}
					path={path}
					limitBase={`${limitBase}.${spec.key}`}
					optional={spec.optional}
					label={spec.label}
				/>
			);
	}
}

function FieldFrame({
	id,
	label,
	hint,
	children,
}: {
	id: string;
	label: string;
	hint?: string;
	children: React.ReactNode;
}) {
	return (
		<div className="grid content-start gap-1.5">
			<label htmlFor={id} className="text-[13px] font-semibold">
				{label}
			</label>
			{children}
			{hint ? (
				<small id={`${id}-hint`} className="text-[12.5px] text-muted-foreground">
					{hint}
				</small>
			) : null}
		</div>
	);
}

function PlainField({
	spec,
	value,
	onChange,
	path,
}: {
	spec: Extract<FieldSpec, { k: "plain" }>;
	value: unknown;
	onChange: (v: unknown) => void;
	path: string;
}) {
	const id = fieldId(path);
	return (
		<FieldFrame id={id} label={spec.label} hint={spec.hint}>
			<Input
				id={id}
				value={String(value ?? "")}
				placeholder={spec.placeholder}
				aria-describedby={spec.hint ? `${id}-hint` : undefined}
				onChange={(e) => onChange(e.target.value)}
			/>
		</FieldFrame>
	);
}

function NumberField({
	spec,
	value,
	onChange,
	path,
}: {
	spec: Extract<FieldSpec, { k: "number" }>;
	value: unknown;
	onChange: (v: unknown) => void;
	path: string;
}) {
	const id = fieldId(path);
	return (
		<FieldFrame id={id} label={spec.label} hint={spec.hint}>
			<Input
				id={id}
				inputMode="numeric"
				className="max-w-40"
				value={value == null ? "" : String(value)}
				aria-describedby={spec.hint ? `${id}-hint` : undefined}
				onChange={(e) => {
					const digits = e.target.value.replace(/\D/g, "");
					onChange(digits === "" ? 0 : Number.parseInt(digits, 10));
				}}
			/>
		</FieldFrame>
	);
}

function SwitchField({
	spec,
	value,
	onChange,
	path,
}: {
	spec: Extract<FieldSpec, { k: "switch" }>;
	value: boolean;
	onChange: (v: unknown) => void;
	path: string;
}) {
	const id = fieldId(path);
	return (
		<div className="flex items-start gap-3">
			<Switch id={id} checked={value} onCheckedChange={onChange} className="mt-0.5" />
			<div className="grid">
				<label htmlFor={id} className="text-[13px] font-semibold">
					{spec.label}
				</label>
				{spec.hint ? <small className="text-[12.5px] text-muted-foreground">{spec.hint}</small> : null}
			</div>
		</div>
	);
}

function SelectField({
	spec,
	value,
	onChange,
	path,
}: {
	spec: Extract<FieldSpec, { k: "select" }>;
	value: string;
	onChange: (v: unknown) => void;
	path: string;
}) {
	const id = fieldId(path);
	return (
		<FieldFrame id={id} label={spec.label}>
			<NativeSelect className="max-w-xs" id={id} value={value} onChange={(e) => onChange(e.target.value)}>
				{spec.options.map((o) => (
					<option key={o.value} value={o.value}>
						{o.label}
					</option>
				))}
			</NativeSelect>
		</FieldFrame>
	);
}

function MultiField({
	spec,
	value,
	onChange,
}: {
	spec: Extract<FieldSpec, { k: "multi" }>;
	value: string[];
	onChange: (v: unknown) => void;
}) {
	const uid = useId();
	return (
		<fieldset className="grid gap-2">
			<legend className="text-[13px] font-semibold">{spec.label}</legend>
			{spec.options.map((o) => (
				<label key={o.value} htmlFor={`${uid}-${o.value}`} className="flex items-center gap-2 text-[13.5px]">
					<Checkbox
						id={`${uid}-${o.value}`}
						checked={value.includes(o.value)}
						onCheckedChange={(on) => onChange(on ? [...value, o.value] : value.filter((v) => v !== o.value))}
					/>
					{o.label}
				</label>
			))}
		</fieldset>
	);
}

function AssetField({
	spec,
	value,
	onChange,
	path,
}: {
	spec: Extract<FieldSpec, { k: "asset" }>;
	value: string;
	onChange: (v: unknown) => void;
	path: string;
}) {
	const draft = useDraft();
	const id = fieldId(path);
	const choices = draft.state.assets.filter((a) => a.kind === spec.assetKind);
	return (
		<FieldFrame id={id} label={spec.label} hint={choices.length === 0 ? t.media.noneYet : undefined}>
			<NativeSelect className="max-w-sm" id={id} value={value} onChange={(e) => onChange(e.target.value)}>
				<option value="">{t.media.choose}</option>
				{choices.map((a) => (
					<option key={a.id} value={a.id}>
						{fill(t.media.option, { kind: t.media.kinds[a.kind], id: a.id.slice(-4) })}
					</option>
				))}
			</NativeSelect>
		</FieldFrame>
	);
}

function PlansField({
	spec,
	value,
	onChange,
}: {
	spec: Extract<FieldSpec, { k: "plans" }>;
	value: string[];
	onChange: (v: unknown) => void;
}) {
	const uid = useId();
	const plans = usePlanChoices();
	return (
		<fieldset className="grid gap-2">
			<legend className="text-[13px] font-semibold">{spec.label}</legend>
			{plans.data?.map((p) => (
				<label key={p.id} htmlFor={`${uid}-${p.id}`} className="flex items-center gap-2 text-[13.5px]">
					<Checkbox
						id={`${uid}-${p.id}`}
						checked={value.includes(p.id)}
						disabled={!value.includes(p.id) && value.length >= spec.max}
						onCheckedChange={(on) => onChange(on ? [...value, p.id] : value.filter((v) => v !== p.id))}
					/>
					{p.name}
				</label>
			))}
			{spec.hint ? <small className="text-[12.5px] text-muted-foreground">{spec.hint}</small> : null}
		</fieldset>
	);
}

function ListField({
	spec,
	value,
	onChange,
	path,
	limitBase,
}: {
	spec: Extract<FieldSpec, { k: "list" }>;
	value: unknown[];
	onChange: (v: unknown) => void;
	path: string;
	limitBase: string;
}) {
	const min = spec.min ?? 0;
	const fixed = spec.min != null && spec.min === spec.max;
	const move = (i: number, delta: -1 | 1) => {
		const next = [...value];
		const [item] = next.splice(i, 1);
		next.splice(i + delta, 0, item);
		onChange(next);
	};
	return (
		<fieldset className="grid gap-3" data-testid={`list-${path}`}>
			<legend className="text-[13px] font-semibold">
				{spec.label}
				<span className="ml-2 font-normal text-muted-foreground">
					{fill(t.list.count, { n: value.length, max: spec.max })}
				</span>
			</legend>
			{value.map((item, i) => (
				// biome-ignore lint/suspicious/noArrayIndexKey: list items have no id; order is the identity
				<div key={i} className="grid gap-3 rounded-sm border border-border bg-secondary/40 p-3">
					<div className="flex items-center justify-between gap-2">
						<span className="text-[12.5px] font-semibold text-foreground-2">
							{spec.itemLabel} {i + 1}
						</span>
						{fixed ? null : (
							<div className="flex gap-1">
								<Button
									type="button"
									size="icon-sm"
									variant="ghost"
									disabled={i === 0}
									aria-label={fill(t.list.up, { item: `${spec.itemLabel} ${i + 1}` })}
									onClick={() => move(i, -1)}
								>
									<ArrowUp aria-hidden />
								</Button>
								<Button
									type="button"
									size="icon-sm"
									variant="ghost"
									disabled={i === value.length - 1}
									aria-label={fill(t.list.down, { item: `${spec.itemLabel} ${i + 1}` })}
									onClick={() => move(i, 1)}
								>
									<ArrowDown aria-hidden />
								</Button>
								<Button
									type="button"
									size="icon-sm"
									variant="ghost"
									disabled={value.length <= min}
									aria-label={fill(t.list.remove, { item: `${spec.itemLabel} ${i + 1}` })}
									onClick={() => onChange(value.filter((_, j) => j !== i))}
								>
									<Trash2 aria-hidden />
								</Button>
							</div>
						)}
					</div>
					{spec.texts ? (
						<LocalizedField
							path={`${path}[${i}]`}
							label={spec.itemLabel}
							value={item as LText}
							onChange={(next) => onChange(value.map((v, j) => (j === i ? (next ?? { en: "" }) : v)))}
							limitKey={`${limitBase}.${spec.key}`}
						/>
					) : (
						<Fields
							specs={spec.fields ?? []}
							value={item as Rec}
							onChange={(next) => onChange(value.map((v, j) => (j === i ? next : v)))}
							path={`${path}[${i}]`}
							limitBase={`${limitBase}.${spec.key}`}
						/>
					)}
				</div>
			))}
			{fixed || value.length >= spec.max ? null : (
				<div>
					<Button type="button" size="sm" variant="outline" onClick={() => onChange([...value, spec.make()])}>
						<Plus aria-hidden />
						{fill(t.list.add, { item: spec.itemLabel.toLowerCase() })}
					</Button>
				</div>
			)}
		</fieldset>
	);
}

/** A sample lead card (name, masked phone, what they wanted, consent line). */
export function LeadFields({
	value,
	onChange,
	path,
	limitBase,
	optional = false,
	label,
}: {
	value: Rec | null;
	onChange: (v: unknown) => void;
	path: string;
	limitBase: string;
	optional?: boolean;
	label: string;
}) {
	const id = fieldId(`${path}-on`);
	const on = value != null;
	const blank: Rec = {
		name: "",
		phone_masked: "",
		looking_for: { en: "" },
		came_from: "website",
		consent: { en: "" },
	};
	return (
		<fieldset className="grid gap-3 rounded-sm border border-border p-3">
			<legend className="px-1 text-[13px] font-semibold">{label}</legend>
			{optional ? (
				<div className="flex items-center gap-3">
					<Switch id={id} checked={on} onCheckedChange={(v) => onChange(v ? blank : null)} />
					<label htmlFor={id} className="text-[13px] font-semibold">
						{t.conversation.showLead}
					</label>
				</div>
			) : null}
			{on || !optional ? (
				<Fields specs={LEAD_SPEC} value={value ?? blank} onChange={onChange} path={path} limitBase={limitBase} />
			) : null}
		</fieldset>
	);
}

const PAGE_TARGETS = [
	{ value: "home", label: "Home page" },
	{ value: "pricing", label: "Pricing page" },
	{ value: "features", label: "Features page" },
	{ value: "about", label: "About page" },
	{ value: "contact", label: "Contact page" },
];
const ADDRESS_TARGET = "__address";

/** Menu or footer links: a label, and either one of the site's pages or an address. */
function LinksField({
	spec,
	value,
	onChange,
	path,
	limitBase,
}: {
	spec: Extract<FieldSpec, { k: "links" }>;
	value: Rec[];
	onChange: (v: unknown) => void;
	path: string;
	limitBase: string;
}) {
	const set = (i: number, patch: Rec) => onChange(value.map((v, j) => (j === i ? { ...v, ...patch } : v)));
	return (
		<fieldset className="grid gap-3" data-testid={`links-${path}`}>
			<legend className="text-[13px] font-semibold">
				{spec.label}
				<span className="ml-2 font-normal text-muted-foreground">
					{fill(t.list.count, { n: value.length, max: spec.max })}
				</span>
			</legend>
			{value.map((link, i) => {
				const toAddress = link.page == null;
				return (
					// biome-ignore lint/suspicious/noArrayIndexKey: links have no id; their place is their identity
					<div key={i} className="grid gap-3 rounded-sm border border-border bg-secondary/40 p-3">
						<LocalizedField
							path={`${path}[${i}].label`}
							label={t.links.label}
							value={link.label as LText}
							onChange={(v) => set(i, { label: v ?? { en: "" } })}
							limitKey={`${limitBase}.${spec.key}.label`}
						/>
						<div className="grid gap-3 min-[600px]:grid-cols-2">
							<SelectField
								spec={{
									k: "select",
									key: "page",
									label: t.links.goesTo,
									options: [...PAGE_TARGETS, { value: ADDRESS_TARGET, label: t.links.address }],
								}}
								value={toAddress ? ADDRESS_TARGET : String(link.page)}
								path={`${path}[${i}].page`}
								onChange={(v) =>
									v === ADDRESS_TARGET
										? set(i, { page: null, href: link.href ?? "https://" })
										: set(i, { page: v, href: null })
								}
							/>
							{toAddress ? (
								<PlainField
									spec={{ k: "plain", key: "href", label: t.links.addressLabel, hint: t.links.addressHint }}
									value={link.href}
									path={`${path}[${i}].href`}
									onChange={(v) => set(i, { href: v })}
								/>
							) : null}
						</div>
						<div>
							<Button type="button" size="sm" variant="ghost" onClick={() => onChange(value.filter((_, j) => j !== i))}>
								<Trash2 aria-hidden />
								{fill(t.list.remove, { item: `${t.links.item} ${i + 1}` })}
							</Button>
						</div>
					</div>
				);
			})}
			{value.length >= spec.max ? null : (
				<div>
					<Button
						type="button"
						size="sm"
						variant="outline"
						onClick={() => onChange([...value, { label: { en: "" }, page: "home", href: null }])}
					>
						<Plus aria-hidden />
						{fill(t.list.add, { item: t.links.item.toLowerCase() })}
					</Button>
				</div>
			)}
		</fieldset>
	);
}
