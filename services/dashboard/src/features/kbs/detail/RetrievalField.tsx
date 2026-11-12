import { RotateCcw } from "lucide-react";
import { type Ref, useId } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { cn } from "@/lib/utils";
import type { RetrievalFieldDef } from "../retrieval-fields";

export type FormValue = boolean | string | null;

/**
 * One mockup `.adv-row`: label + plain help + "default: …", the control, and a Reset icon button
 * (`aria-label="Reset {label}"`) that sends `null` for the field. Controls are described by the
 * help and default text.
 */
export function RetrievalField({
	def,
	value,
	defaultText,
	fallback,
	onChange,
	onBlur,
	fieldRef,
	onReset,
	resetting,
	error,
	inactive,
}: {
	def: RetrievalFieldDef;
	value: FormValue;
	/** What the engine runs while unset, e.g. "on (engine)"; "engine default" while unknown. */
	defaultText: string;
	/** The engine's value while unset (numbers as text): drawn muted in the control. */
	fallback: FormValue;
	onChange: (v: FormValue) => void;
	/** react-hook-form's field.onBlur: lets `onTouched` validate after the first blur. */
	onBlur?: () => void;
	/** react-hook-form's field.ref: lets a failed submit focus this control. */
	fieldRef?: Ref<HTMLInputElement>;
	onReset: () => void;
	resetting?: boolean;
	error?: string;
	/** Only applies when another setting is on (shown dimmed, still editable). */
	inactive?: boolean;
}) {
	const id = useId();
	const helpId = `${id}-help`;
	const errId = `${id}-err`;
	const describedBy = [helpId, error ? errId : null].filter(Boolean).join(" ");
	const c = def.control;
	const unset = value === null || value === "";
	return (
		<div
			className={cn(
				"grid items-center gap-2 border-b border-border px-4 py-3 min-[861px]:grid-cols-[minmax(0,1fr)_auto] min-[861px]:gap-4",
			)}
			data-field={def.key}
			data-inactive={inactive || undefined}
			data-default={unset || undefined}
		>
			<div className="min-w-0">
				<label htmlFor={id} className="block font-semibold">
					{def.label}
				</label>
				<span id={helpId} className="text-[12.5px] text-muted-foreground">
					{def.help}{" "}
					<span className="font-mono text-[11px] whitespace-nowrap">
						{fill(t.retrieval.defaultLabel, { v: defaultText })}
					</span>
					{unset ? <span className="font-mono text-[11px] whitespace-nowrap"> · {t.retrieval.notSet}</span> : null}
					{inactive ? <span className="font-mono text-[11px]"> · {t.retrieval.notInUse}</span> : null}
				</span>
				{error ? (
					<span id={errId} role="alert" className="block text-[12.5px] font-medium text-destructive">
						{error}
					</span>
				) : null}
			</div>
			<div className="flex items-center gap-2 min-[861px]:justify-end">
				{c.kind === "switch" ? (
					<Switch
						id={id}
						checked={unset ? fallback === true : value === true}
						className={unset ? "opacity-55" : undefined}
						onCheckedChange={(v) => onChange(v)}
						aria-describedby={describedBy}
					/>
				) : c.kind === "number" ? (
					<Input
						id={id}
						inputMode={c.integer ? "numeric" : "decimal"}
						className="w-[88px] text-right font-mono text-[13px]"
						value={typeof value === "string" ? value : ""}
						placeholder={typeof fallback === "string" && fallback !== "" ? fallback : "—"}
						onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
						onBlur={onBlur}
						ref={fieldRef}
						aria-invalid={!!error}
						aria-describedby={describedBy}
					/>
				) : c.kind === "select" ? (
					<NativeSelect
						id={id}
						className={cn("w-[170px]", unset && "text-muted-foreground")}
						value={typeof value === "string" ? value : ""}
						onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
						onBlur={onBlur}
						aria-describedby={describedBy}
					>
						<option value="">{fill(t.retrieval.defaultOption, { v: defaultText })}</option>
						{c.options.map((o) => (
							<option key={o.value} value={o.value}>
								{o.label}
							</option>
						))}
					</NativeSelect>
				) : (
					<Input
						id={id}
						className="w-[170px] font-mono text-[13px]"
						placeholder={typeof fallback === "string" && fallback !== "" ? fallback : defaultText}
						value={typeof value === "string" ? value : ""}
						onChange={(e) => onChange(e.target.value === "" ? null : e.target.value)}
						onBlur={onBlur}
						ref={fieldRef}
						aria-describedby={describedBy}
					/>
				)}
				<Button
					type="button"
					size="icon-sm"
					variant="ghost"
					aria-label={fill(t.retrieval.reset, { label: def.label })}
					title={fill(t.retrieval.reset, { label: def.label })}
					disabled={resetting}
					onClick={onReset}
				>
					<RotateCcw aria-hidden />
				</Button>
			</div>
		</div>
	);
}
