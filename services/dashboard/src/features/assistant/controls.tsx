import { type ReactNode, useId } from "react";
import {
	Controller,
	type FieldValues,
	get,
	type Path,
	type PathValue,
	useFormContext,
	useWatch,
} from "react-hook-form";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Segmented } from "@/components/patterns/Segmented";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { cn } from "@/lib/utils";

/**
 * Small form controls bound to the surrounding `FormProvider` (`SectionShell`), so a section reads
 * as a list of fields. Names are RHF paths; errors come from the zod resolver or the engine.
 */
export function errorAt(errors: unknown, name: string): string | undefined {
	const e = get(errors, name) as { message?: unknown } | undefined;
	return typeof e?.message === "string" ? e.message : undefined;
}

export function useFieldError(name: string): string | undefined {
	const { formState } = useFormContext();
	return errorAt(formState.errors, name);
}

export function Counter({ value, max }: { value: string | null | undefined; max: number }) {
	const n = (value ?? "").length;
	return (
		<span className={cn("font-mono text-[11.5px] tabular-nums text-muted-foreground", n > max && "text-destructive")}>
			{t.form.counter.replace("{n}", String(n)).replace("{max}", String(max))}
		</span>
	);
}

export function TextControl({
	name,
	label,
	hint,
	maxLength,
	type = "text",
	inputMode,
	placeholder,
	rows,
	counter,
	autoComplete,
	labelExtra,
}: {
	name: string;
	label: string;
	hint?: ReactNode;
	maxLength?: number;
	type?: "text" | "url" | "email" | "tel" | "time" | "date";
	inputMode?: "text" | "url" | "email" | "tel";
	placeholder?: string;
	/** Rows > 0 renders a textarea. */
	rows?: number;
	counter?: boolean;
	autoComplete?: string;
	labelExtra?: ReactNode;
}) {
	const id = useId();
	const { register, control } = useFormContext();
	const error = useFieldError(name);
	const value = useWatch({ control, name }) as string | null | undefined;
	const props = {
		id,
		"aria-invalid": error ? true : undefined,
		"aria-describedby": describedBy(id, { hint: !!hint, error: !!error }),
		placeholder,
		...register(name),
	};
	return (
		<Field
			id={id}
			label={label}
			hint={hint}
			error={error}
			labelExtra={labelExtra ?? (counter && maxLength ? <Counter value={value} max={maxLength} /> : undefined)}
		>
			{rows ? (
				<Textarea rows={rows} {...props} />
			) : (
				<Input type={type} inputMode={inputMode} autoComplete={autoComplete} {...props} />
			)}
		</Field>
	);
}

/** A whole number or blank (blank = null when `nullable`). */
export function NumberControl({
	name,
	label,
	hint,
	min,
	max,
	step = 1,
	nullable = false,
	placeholder,
}: {
	name: string;
	label: string;
	hint?: ReactNode;
	min?: number;
	max?: number;
	step?: number;
	nullable?: boolean;
	placeholder?: string;
}) {
	const id = useId();
	const { register } = useFormContext();
	const error = useFieldError(name);
	return (
		<Field id={id} label={label} hint={hint} error={error}>
			<Input
				id={id}
				type="number"
				inputMode="decimal"
				min={min}
				max={max}
				step={step}
				placeholder={placeholder}
				aria-invalid={error ? true : undefined}
				aria-describedby={describedBy(id, { hint: !!hint, error: !!error })}
				{...register(name, {
					setValueAs: (v: unknown) => (v === "" || v == null ? (nullable ? null : Number.NaN) : Number(v)),
				})}
			/>
		</Field>
	);
}

export function SwitchControl({
	name,
	label,
	hint,
	disabled,
}: {
	name: string;
	label: string;
	hint?: ReactNode;
	disabled?: boolean;
}) {
	const id = useId();
	const { control, setValue } = useFormContext();
	const value = useWatch({ control, name }) as boolean | undefined;
	return (
		<div className="grid gap-1">
			<div className="flex items-center gap-2.5">
				<Switch
					id={id}
					checked={!!value}
					disabled={disabled}
					aria-describedby={hint ? `${id}-hint` : undefined}
					onCheckedChange={(v) => setValue(name, v, { shouldDirty: true, shouldValidate: true })}
				/>
				<label htmlFor={id} className="text-[13.5px] font-semibold">
					{label}
				</label>
			</div>
			{hint ? (
				<small id={`${id}-hint`} className="pl-[42px] text-[12.5px] text-muted-foreground">
					{hint}
				</small>
			) : null}
		</div>
	);
}

/** A few options as segmented radio buttons (arrow keys move the choice). */
export function ChoiceControl<V extends FieldValues, T extends string>({
	name,
	label,
	options,
	hint,
	describe,
}: {
	name: Path<V>;
	label: string;
	options: { value: T; label: ReactNode }[];
	hint?: ReactNode;
	describe?: (value: T) => ReactNode;
}) {
	const { control } = useFormContext<V>();
	const error = useFieldError(name);
	return (
		<Controller
			control={control}
			name={name}
			render={({ field }) => (
				<div className="grid content-start gap-1.5">
					<span className="text-[13px] font-semibold" id={`${name}-label`}>
						{label}
					</span>
					<div className="flex flex-wrap">
						<Segmented<T>
							label={label}
							value={field.value as T}
							options={options}
							onChange={(v) => field.onChange(v as PathValue<V, Path<V>>)}
						/>
					</div>
					{describe && field.value ? (
						<small className="text-[12.5px] text-muted-foreground">{describe(field.value as T)}</small>
					) : null}
					{hint ? <small className="text-[12.5px] text-muted-foreground">{hint}</small> : null}
					{error ? <small className="text-[12.5px] font-medium text-destructive">{error}</small> : null}
				</div>
			)}
		/>
	);
}

export function SelectControl({
	name,
	label,
	options,
	hint,
	emptyLabel,
	numeric,
}: {
	name: string;
	label: string;
	options: { value: string; label: string }[];
	hint?: ReactNode;
	/** An option with value "" (e.g. "Any"). */
	emptyLabel?: string;
	/** The field holds a number: the chosen option's value is stored as one. */
	numeric?: boolean;
}) {
	const id = useId();
	const { register } = useFormContext();
	const error = useFieldError(name);
	return (
		<Field id={id} label={label} hint={hint} error={error}>
			<NativeSelect
				id={id}
				aria-invalid={error ? true : undefined}
				aria-describedby={describedBy(id, { hint: !!hint, error: !!error })}
				{...register(name, { setValueAs: (v: string) => (numeric ? Number(v) : v) })}
			>
				{emptyLabel !== undefined ? <option value="">{emptyLabel}</option> : null}
				{options.map((o) => (
					<option key={o.value} value={o.value}>
						{o.label}
					</option>
				))}
			</NativeSelect>
		</Field>
	);
}

/** A select whose empty choice is `null` in the form (rule conditions, topic links). */
export function NullableSelectControl({
	name,
	label,
	options,
	emptyLabel,
}: {
	name: string;
	label: string;
	options: { value: string; label: string }[];
	emptyLabel: string;
}) {
	const id = useId();
	const { register } = useFormContext();
	return (
		<Field id={id} label={label}>
			<NativeSelect id={id} {...register(name, { setValueAs: (v: string) => (v === "" ? null : v) })}>
				<option value="">{emptyLabel}</option>
				{options.map((o) => (
					<option key={o.value} value={o.value}>
						{o.label}
					</option>
				))}
			</NativeSelect>
		</Field>
	);
}

/** A list of short texts as removable chips plus an input (service areas, keywords, banned phrases). */
export function ChipsControl({
	name,
	label,
	hint,
	max,
	maxLength,
	addLabel,
	placeholder,
}: {
	name: string;
	label: string;
	hint?: ReactNode;
	max: number;
	maxLength: number;
	addLabel: string;
	placeholder?: string;
}) {
	const id = useId();
	const { control, setValue, formState } = useFormContext();
	const items = (useWatch({ control, name }) as string[] | undefined) ?? [];
	// One message for the list, or the first one among its items (e.g. an address that is not valid).
	const error =
		errorAt(formState.errors, name) ??
		items.map((_, i) => errorAt(formState.errors, `${name}.${i}`)).find((m) => m !== undefined);
	const add = (el: HTMLInputElement) => {
		const v = el.value.trim();
		if (!v || items.length >= max || items.includes(v)) return;
		setValue(name, [...items, v], { shouldDirty: true, shouldValidate: true });
		el.value = "";
	};
	return (
		<Field id={id} label={label} hint={hint} error={error}>
			<div className="grid gap-2">
				{items.length ? (
					<ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
						{items.map((it, i) => (
							<li
								key={`${it}-${
									// biome-ignore lint/suspicious/noArrayIndexKey: chips are text; two equal ones are refused above
									i
								}`}
								className="inline-flex items-center gap-1 rounded-full border border-border bg-secondary py-0.5 pr-1 pl-2.5 text-[12.5px]"
							>
								{it}
								<button
									type="button"
									aria-label={t.form.removeItem.replace("{what}", it)}
									className="grid size-5 place-items-center rounded-full text-muted-foreground hover:bg-accent hover:text-foreground"
									onClick={() =>
										setValue(
											name,
											items.filter((_, j) => j !== i),
											{ shouldDirty: true, shouldValidate: true },
										)
									}
								>
									<span aria-hidden>×</span>
								</button>
							</li>
						))}
					</ul>
				) : null}
				<Input
					id={id}
					placeholder={placeholder ?? addLabel}
					maxLength={maxLength}
					disabled={items.length >= max}
					aria-label={label}
					onKeyDown={(e) => {
						if (e.key === "Enter") {
							e.preventDefault();
							add(e.currentTarget);
						}
					}}
					onBlur={(e) => add(e.currentTarget)}
				/>
			</div>
		</Field>
	);
}

/** A short list of texts, one textarea each (voice examples). Items are added and removed. */
export function StringListControl({
	name,
	label,
	hint,
	max,
	maxLength,
	itemLabel,
	addLabel,
	rows = 2,
}: {
	name: string;
	label: string;
	hint?: ReactNode;
	max: number;
	maxLength: number;
	/** `{n}` is the 1-based position. */
	itemLabel: string;
	addLabel: string;
	rows?: number;
}) {
	const { control, setValue, formState } = useFormContext();
	const items = (useWatch({ control, name }) as string[] | undefined) ?? [];
	const set = (next: string[]) => setValue(name, next, { shouldDirty: true, shouldValidate: formState.isSubmitted });
	return (
		<fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0">
			<legend className="mb-1 p-0 text-[13px] font-semibold">{label}</legend>
			{hint ? <small className="text-[12.5px] text-muted-foreground">{hint}</small> : null}
			{items.map((it, i) => {
				const error = errorAt(formState.errors, `${name}.${i}`);
				return (
					<div
						// biome-ignore lint/suspicious/noArrayIndexKey: plain strings have no id; rows are only added or removed at the end/by index
						key={i}
						className="grid gap-1"
					>
						<div className="flex items-start gap-2">
							<Textarea
								rows={rows}
								className="flex-1"
								value={it}
								aria-label={itemLabel.replace("{n}", String(i + 1))}
								aria-invalid={error ? true : undefined}
								onChange={(e) => set(items.map((x, j) => (j === i ? e.target.value : x)))}
							/>
							<button
								type="button"
								className="h-8 rounded-sm px-2 text-[12.5px] font-semibold text-foreground-2 hover:bg-accent"
								aria-label={t.form.removeItem.replace("{what}", itemLabel.replace("{n}", String(i + 1)))}
								onClick={() => set(items.filter((_, j) => j !== i))}
							>
								{t.form.remove}
							</button>
						</div>
						<div className="flex justify-between">
							{error ? <small className="text-[12.5px] font-medium text-destructive">{error}</small> : <span />}
							<Counter value={it} max={maxLength} />
						</div>
					</div>
				);
			})}
			<div>
				<button
					type="button"
					disabled={items.length >= max}
					className="h-[30px] rounded-sm border border-input bg-card px-2.5 text-[12.5px] font-semibold hover:bg-accent disabled:opacity-45"
					onClick={() => set([...items, ""])}
				>
					{addLabel}
				</button>
			</div>
		</fieldset>
	);
}

/**
 * An optional whole number: a switch turns it on (with `defaultValue`) or off (null in the form).
 * Controlled, not registered, so switching it off never leaves an empty input behind to validate.
 */
export function OptionalNumberControl({
	name,
	label,
	numberLabel,
	min,
	max,
	defaultValue,
	hint,
}: {
	name: string;
	label: string;
	numberLabel: string;
	min: number;
	max: number;
	defaultValue: number;
	hint?: ReactNode;
}) {
	const id = useId();
	const { control, setValue } = useFormContext();
	const value = useWatch({ control, name }) as number | null | undefined;
	const error = useFieldError(name);
	const on = value !== null && value !== undefined;
	return (
		<div className="grid gap-1.5">
			<div className="flex flex-wrap items-center gap-3">
				<div className="flex items-center gap-2.5">
					<Switch
						id={`${id}-on`}
						checked={on}
						onCheckedChange={(v) =>
							setValue(name, v ? defaultValue : null, { shouldDirty: true, shouldValidate: true })
						}
					/>
					<label htmlFor={`${id}-on`} className="text-[13.5px] font-semibold">
						{label}
					</label>
				</div>
				{on ? (
					<Input
						type="number"
						inputMode="numeric"
						min={min}
						max={max}
						className="w-24"
						aria-label={numberLabel}
						aria-invalid={error ? true : undefined}
						value={Number.isNaN(value) ? "" : String(value)}
						onChange={(e) =>
							setValue(name, e.target.value === "" ? Number.NaN : Number(e.target.value), {
								shouldDirty: true,
								shouldValidate: true,
							})
						}
					/>
				) : null}
			</div>
			{hint ? <small className="pl-[42px] text-[12.5px] text-muted-foreground">{hint}</small> : null}
			{error ? <small className="pl-[42px] text-[12.5px] font-medium text-destructive">{error}</small> : null}
		</div>
	);
}

/** A compact registered input with its error below, for rows of a list (starters, buttons, holidays). */
export function InlineInput({
	name,
	label,
	maxLength,
	type = "text",
	className,
}: {
	name: string;
	label: string;
	maxLength?: number;
	type?: "text" | "date" | "time";
	className?: string;
}) {
	const { register } = useFormContext();
	const error = useFieldError(name);
	return (
		<div className={cn("grid gap-0.5", className)}>
			<input
				type={type}
				maxLength={maxLength}
				aria-label={label}
				aria-invalid={error ? true : undefined}
				className="h-[34px] w-full rounded-sm border border-input bg-muted px-2 text-sm"
				{...register(name)}
			/>
			{error ? <small className="text-[12px] font-medium text-destructive">{error}</small> : null}
		</div>
	);
}
