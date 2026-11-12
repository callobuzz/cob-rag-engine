import { useId } from "react";
import { describedBy, Field } from "@/components/auth/Field";
import { Segmented } from "@/components/patterns/Segmented";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { fill } from "@/lib/strings/en";
import { widgetStrings as t } from "@/lib/strings/widget";
import { cn } from "@/lib/utils";
import { APPEARANCE_DEFAULTS, type AppearanceForm, HEX_COLOR, whiteTextContrast } from "./schemas";

export type AppearanceErrors = Partial<Record<keyof AppearanceForm, string | undefined>>;

/**
 * Every `WidgetAppearance` field: title, greeting, placeholder, brand colour (picker + hex +
 * contrast message), position and show sources. Controlled; empty text falls back to the
 * widget's default (shown as the placeholder).
 */
export function AppearanceFields({
	value,
	onChange,
	errors = {},
	disabled,
}: {
	value: AppearanceForm;
	onChange: (patch: Partial<AppearanceForm>) => void;
	errors?: AppearanceErrors;
	disabled?: boolean;
}) {
	const base = useId();
	const ids = {
		title: `${base}-title`,
		greeting: `${base}-greeting`,
		placeholder: `${base}-placeholder`,
		color: `${base}-color`,
		sources: `${base}-sources`,
	};
	const hex = value.primary_color.trim();
	const pickerValue = HEX_COLOR.test(hex) ? hex.toLowerCase() : APPEARANCE_DEFAULTS.primary_color;
	const contrast = whiteTextContrast(pickerValue);
	const contrastId = `${ids.color}-contrast`;
	const ratio = contrast.ratio.toFixed(1);

	return (
		<fieldset disabled={disabled} className="m-0 grid min-w-0 gap-4 border-0 p-0">
			<Field id={ids.title} label={t.appearance.titleField} hint={t.appearance.titleHint} error={errors.title}>
				<Input
					id={ids.title}
					value={value.title}
					placeholder={APPEARANCE_DEFAULTS.title}
					autoComplete="off"
					aria-invalid={!!errors.title}
					aria-describedby={describedBy(ids.title, { hint: true, error: !!errors.title })}
					onChange={(e) => onChange({ title: e.target.value })}
				/>
			</Field>
			<Field id={ids.greeting} label={t.appearance.greeting} hint={t.appearance.greetingHint} error={errors.greeting}>
				<Textarea
					id={ids.greeting}
					value={value.greeting}
					rows={2}
					placeholder={APPEARANCE_DEFAULTS.greeting}
					aria-invalid={!!errors.greeting}
					aria-describedby={describedBy(ids.greeting, { hint: true, error: !!errors.greeting })}
					onChange={(e) => onChange({ greeting: e.target.value })}
				/>
			</Field>
			<Field
				id={ids.placeholder}
				label={t.appearance.placeholder}
				hint={t.appearance.placeholderHint}
				error={errors.placeholder}
			>
				<Input
					id={ids.placeholder}
					value={value.placeholder}
					placeholder={APPEARANCE_DEFAULTS.placeholder}
					autoComplete="off"
					aria-invalid={!!errors.placeholder}
					aria-describedby={describedBy(ids.placeholder, { hint: true, error: !!errors.placeholder })}
					onChange={(e) => onChange({ placeholder: e.target.value })}
				/>
			</Field>
			<Field id={ids.color} label={t.appearance.color} hint={t.appearance.colorHint} error={errors.primary_color}>
				<div className="flex items-center gap-2">
					<input
						type="color"
						aria-label={t.appearance.colorPicker}
						value={pickerValue}
						onChange={(e) => onChange({ primary_color: e.target.value })}
						className="h-9 w-12 shrink-0 cursor-pointer rounded-md border border-input bg-card p-1 disabled:cursor-not-allowed"
					/>
					<Input
						id={ids.color}
						value={value.primary_color}
						placeholder={APPEARANCE_DEFAULTS.primary_color}
						spellCheck={false}
						autoComplete="off"
						className="max-w-[160px] font-mono"
						aria-invalid={!!errors.primary_color}
						aria-describedby={[describedBy(ids.color, { hint: true, error: !!errors.primary_color }), contrastId].join(
							" ",
						)}
						onChange={(e) => onChange({ primary_color: e.target.value })}
					/>
				</div>
				<small
					id={contrastId}
					data-testid="contrast-hint"
					data-ok={contrast.ok}
					className={cn("text-[12.5px]", contrast.ok ? "text-muted-foreground" : "font-medium text-warning")}
				>
					{fill(contrast.ok ? t.appearance.contrastOk : t.appearance.contrastLow, { ratio })}
				</small>
			</Field>
			<div className="grid gap-1.5">
				<span className="text-[13px] font-semibold" id={`${base}-position`}>
					{t.appearance.position}
				</span>
				<div>
					<Segmented
						label={t.appearance.position}
						value={value.position}
						options={[
							{ value: "right", label: t.appearance.right },
							{ value: "left", label: t.appearance.left },
						]}
						onChange={(position) => {
							if (!disabled) onChange({ position });
						}}
					/>
				</div>
			</div>
			<div className="flex items-start justify-between gap-4">
				<div className="grid gap-0.5">
					<label htmlFor={ids.sources} className="text-[13px] font-semibold">
						{t.appearance.showSources}
					</label>
					<small id={`${ids.sources}-hint`} className="text-[12.5px] text-muted-foreground">
						{t.appearance.showSourcesHint}
					</small>
				</div>
				<Switch
					id={ids.sources}
					checked={value.show_sources}
					aria-describedby={`${ids.sources}-hint`}
					onCheckedChange={(show_sources) => onChange({ show_sources })}
				/>
			</div>
		</fieldset>
	);
}
