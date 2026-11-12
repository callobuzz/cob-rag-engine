import { useId } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Checkbox } from "@/components/ui/checkbox";
import { contentStrings as t } from "@/lib/strings/content";
import type { Mapping } from "./records";

/**
 * Step 2: which column is the ID, the text, the title and the URL (real labelled <select>s);
 * every other column can go in as metadata.
 */
export function RecordsMapping({
	columns,
	mapping,
	onChange,
	showErrors,
}: {
	columns: string[];
	mapping: Mapping;
	onChange: (m: Mapping) => void;
	showErrors: boolean;
}) {
	const chosen = new Set([mapping.id, mapping.text, mapping.title, mapping.url].filter(Boolean));
	const others = columns.filter((c) => !chosen.has(c));
	const setField = (field: "id" | "text" | "title" | "url", value: string) => {
		const next: Mapping = { ...mapping, [field]: value };
		// A column is used once: taking it for a field removes it from the others and from metadata.
		for (const f of ["id", "text", "title", "url"] as const) if (f !== field && next[f] === value) next[f] = "";
		const taken = new Set([next.id, next.text, next.title, next.url].filter(Boolean));
		next.metadata = next.metadata.filter((c) => !taken.has(c));
		const freed = [mapping.id, mapping.text, mapping.title, mapping.url].filter((c) => c && !taken.has(c));
		onChange({ ...next, metadata: [...next.metadata, ...freed.filter((c) => !next.metadata.includes(c))] });
	};
	return (
		<div className="grid gap-5">
			<p className="m-0 text-[13.5px] text-foreground-2">{t.records.mappingLead}</p>
			<div className="grid gap-4 min-[861px]:grid-cols-2">
				<FieldSelect
					label={t.records.id}
					hint={t.records.idHint}
					required
					value={mapping.id}
					columns={columns}
					invalid={showErrors && !mapping.id}
					onChange={(v) => setField("id", v)}
				/>
				<FieldSelect
					label={t.records.text}
					hint={t.records.textHint}
					required
					value={mapping.text}
					columns={columns}
					invalid={showErrors && !mapping.text}
					onChange={(v) => setField("text", v)}
				/>
				<FieldSelect
					label={t.records.title}
					value={mapping.title}
					columns={columns}
					onChange={(v) => setField("title", v)}
				/>
				<FieldSelect label={t.records.url} value={mapping.url} columns={columns} onChange={(v) => setField("url", v)} />
			</div>
			<fieldset className="grid gap-2 border-0 p-0">
				<legend className="mb-1 text-[13px] font-semibold">{t.records.metadata}</legend>
				<p className="m-0 text-[12.5px] text-muted-foreground">{t.records.metadataHint}</p>
				{others.length === 0 ? (
					<p className="m-0 text-[13px] text-muted-foreground">{t.records.noMetadata}</p>
				) : (
					<div className="flex flex-wrap gap-x-5 gap-y-2">
						{others.map((c) => (
							<MetaCheck
								key={c}
								column={c}
								checked={mapping.metadata.includes(c)}
								onChange={(on) =>
									onChange({
										...mapping,
										metadata: on ? [...mapping.metadata, c] : mapping.metadata.filter((x) => x !== c),
									})
								}
							/>
						))}
					</div>
				)}
			</fieldset>
		</div>
	);
}

function MetaCheck({
	column,
	checked,
	onChange,
}: {
	column: string;
	checked: boolean;
	onChange: (on: boolean) => void;
}) {
	const id = useId();
	return (
		<div className="flex items-center gap-2">
			<Checkbox id={id} checked={checked} onCheckedChange={(v) => onChange(v === true)} />
			<label htmlFor={id} className="font-mono text-[12.5px]">
				{column}
			</label>
		</div>
	);
}

function FieldSelect({
	label,
	hint,
	required,
	value,
	columns,
	invalid,
	onChange,
}: {
	label: string;
	hint?: string;
	required?: boolean;
	value: string;
	columns: string[];
	invalid?: boolean;
	onChange: (v: string) => void;
}) {
	const id = useId();
	const hintId = useId();
	return (
		<div className="grid gap-1.5">
			<label htmlFor={id} className="text-[13px] font-semibold">
				{label}
				{required ? <span className="text-destructive"> *</span> : null}
			</label>
			<NativeSelect
				id={id}
				value={value}
				required={required}
				aria-invalid={invalid || undefined}
				aria-describedby={hint ? hintId : undefined}
				onChange={(e) => onChange(e.target.value)}
			>
				<option value="">{required ? t.records.pickColumn : t.records.none}</option>
				{columns.map((c) => (
					<option key={c} value={c}>
						{c}
					</option>
				))}
			</NativeSelect>
			{hint ? (
				<p id={hintId} className="m-0 text-[12.5px] text-muted-foreground">
					{hint}
				</p>
			) : null}
		</div>
	);
}
