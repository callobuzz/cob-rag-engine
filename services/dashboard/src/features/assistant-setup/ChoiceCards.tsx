import { useId } from "react";
import { cn } from "@/lib/utils";

export interface Choice {
	value: string;
	label: string;
	hint?: string;
}

/**
 * Single or multiple choice as cards. Each card is a real `<input type="radio|checkbox">` inside
 * its `<label>`, so keyboard, screen readers and `getByRole("radio" | "checkbox")` work without
 * any custom roles; the arrow keys of a radio group move the choice natively.
 */
export function ChoiceCards({
	legend,
	type,
	name,
	choices,
	value,
	onChange,
	invalid,
	describedBy,
	columns = true,
}: {
	legend: string;
	type: "radio" | "checkbox";
	name: string;
	choices: Choice[];
	value: string[];
	onChange: (next: string[]) => void;
	invalid?: boolean;
	describedBy?: string;
	columns?: boolean;
}) {
	const base = useId();
	return (
		<fieldset className="m-0 min-w-0 border-0 p-0" aria-describedby={describedBy} aria-invalid={invalid || undefined}>
			<legend className="sr-only">{legend}</legend>
			<div className={cn("grid gap-2", columns && "min-[620px]:grid-cols-2")}>
				{choices.map((c) => {
					const id = `${base}-${c.value}`;
					const on = value.includes(c.value);
					return (
						<label
							key={c.value}
							htmlFor={id}
							className={cn(
								"flex cursor-pointer items-start gap-3 rounded-md border border-border bg-card p-3 text-left transition-[border-color] duration-[var(--t)] hover:border-input has-[:focus-visible]:shadow-[0_0_0_3px_var(--primary-soft)]",
								on && "border-primary bg-primary-soft",
								invalid && !on && "border-destructive",
							)}
						>
							<input
								id={id}
								type={type}
								name={name}
								value={c.value}
								checked={on}
								onChange={() => {
									if (type === "radio") onChange([c.value]);
									else onChange(on ? value.filter((v) => v !== c.value) : [...value, c.value]);
								}}
								className="mt-0.5 size-4 shrink-0 accent-[var(--primary)]"
							/>
							<span className="grid gap-0.5">
								<span className="text-[14px] font-semibold">{c.label}</span>
								{c.hint ? <span className="text-[12.5px] text-muted-foreground">{c.hint}</span> : null}
							</span>
						</label>
					);
				})}
			</div>
		</fieldset>
	);
}
