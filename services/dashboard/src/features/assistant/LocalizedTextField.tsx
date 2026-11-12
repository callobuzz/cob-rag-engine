import { type ReactNode, useId, useState } from "react";
import { useFormContext, useWatch } from "react-hook-form";
import { Textarea } from "@/components/ui/textarea";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { cn } from "@/lib/utils";
import { Counter, errorAt } from "./controls";
import { tabLangs } from "./defaults";
import type { LangKey } from "./schemas";

/**
 * One text per language (`LocalizedText`): tabs for English, Hindi, Hinglish and any other language
 * the assistant supports; empty means the English text is used. The value lives in the form at
 * `name` as `{ [lang]: text }` (or null when the field is optional and nothing is set).
 */
export function LocalizedTextField({
	name,
	label,
	max,
	hint,
	languages,
	rows = 3,
	placeholder,
}: {
	name: string;
	label: string;
	max: number;
	hint?: ReactNode;
	/** The editable languages; the form's current `language.supported`. */
	languages?: readonly LangKey[];
	rows?: number;
	placeholder?: string;
}) {
	const id = useId();
	const { control, setValue, formState } = useFormContext();
	const value = (useWatch({ control, name }) as Partial<Record<LangKey, string>> | null | undefined) ?? {};
	const langs = tabLangs(languages);
	const [active, setActive] = useState<LangKey>("en");
	const shown = langs.includes(active) ? active : "en";
	const error = errorAt(formState.errors, `${name}.${shown}`) ?? errorAt(formState.errors, name);
	const anyError = (l: LangKey) => !!errorAt(formState.errors, `${name}.${l}`);
	return (
		<fieldset className="m-0 grid min-w-0 content-start gap-1.5 border-0 p-0" aria-labelledby={`${id}-label`}>
			<div className="flex items-baseline justify-between gap-3">
				<span id={`${id}-label`} className="text-[13px] font-semibold">
					{label}
				</span>
				<Counter value={value[shown]} max={max} />
			</div>
			<div role="tablist" aria-label={`${label}: ${t.form.emptyUsesEnglish}`} className="flex flex-wrap gap-1">
				{langs.map((l) => (
					<button
						key={l}
						type="button"
						role="tab"
						id={`${id}-tab-${l}`}
						aria-selected={l === shown}
						aria-controls={`${id}-panel`}
						tabIndex={l === shown ? 0 : -1}
						onClick={() => setActive(l)}
						onKeyDown={(e) => {
							if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
							e.preventDefault();
							const i = langs.indexOf(l);
							const next = langs[(i + (e.key === "ArrowRight" ? 1 : langs.length - 1)) % langs.length];
							if (next) {
								setActive(next);
								document.getElementById(`${id}-tab-${next}`)?.focus();
							}
						}}
						className={cn(
							"h-7 rounded-md border px-2.5 text-[12.5px] font-semibold",
							l === shown
								? "border-primary bg-primary-soft text-foreground"
								: "border-border bg-secondary text-foreground-2 hover:bg-accent",
							anyError(l) && "border-destructive",
						)}
					>
						{t.langs[l]}
						{(value[l] ?? "").trim() ? <span className="sr-only"> (has text)</span> : null}
					</button>
				))}
			</div>
			<div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-tab-${shown}`}>
				<Textarea
					rows={rows}
					lang={shown === "hi-Latn" ? "hi-Latn" : shown}
					aria-label={`${label} (${t.langs[shown]})`}
					aria-invalid={error ? true : undefined}
					placeholder={shown === "en" ? placeholder : (value.en ?? placeholder)}
					value={value[shown] ?? ""}
					onChange={(e) => {
						const next = { ...value, [shown]: e.target.value };
						setValue(name, next, { shouldDirty: true, shouldValidate: formState.isSubmitted || !!error });
					}}
					onBlur={() => void 0}
				/>
			</div>
			{hint ? <small className="text-[12.5px] text-muted-foreground">{hint}</small> : null}
			{error ? <small className="text-[12.5px] font-medium text-destructive">{error}</small> : null}
		</fieldset>
	);
}
