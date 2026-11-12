import type { KeyboardEvent } from "react";
import { Badge } from "@/components/ui/badge";
import { fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { cn } from "@/lib/utils";

export const PRESET_ORDER = ["economy", "standard", "premium"] as const;

export function presetLabel(name: string): string {
	return t.retrieval.presets[name]?.name ?? name.charAt(0).toUpperCase() + name.slice(1);
}

/**
 * Mockup `.presets`: three cards as a radio group (arrow keys move the choice). `presets` comes
 * from `GET /api/v1/models/presets`; when it failed, the three known names still show.
 */
export function PresetCards({
	label,
	value,
	current,
	presets,
	onChange,
	disabled,
}: {
	label: string;
	value: string | null;
	current?: string | null;
	presets: Record<string, Record<string, unknown>> | undefined;
	onChange: (name: string) => void;
	disabled?: boolean;
}) {
	const names = presets
		? [
				...PRESET_ORDER.filter((n) => n in presets),
				...Object.keys(presets).filter((n) => !PRESET_ORDER.includes(n as never)),
			]
		: [...PRESET_ORDER];
	const focusIndex = Math.max(0, value ? names.indexOf(value) : 0);
	const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
		if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)) return;
		e.preventDefault();
		const dir = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : names.length - 1;
		const next = names[(focusIndex + dir) % names.length];
		if (!next) return;
		if (!disabled) onChange(next);
		e.currentTarget.querySelector<HTMLButtonElement>(`[data-preset="${next}"]`)?.focus();
	};
	return (
		<div role="radiogroup" aria-label={label} onKeyDown={onKey} className="grid gap-3 min-[861px]:grid-cols-3">
			{names.map((name, i) => {
				const on = value === name;
				const v = presets?.[name];
				const top = typeof v?.top_k === "number" ? v.top_k : null;
				const fin = typeof v?.final_k === "number" ? v.final_k : null;
				return (
					// biome-ignore lint/a11y/useSemanticElements: card-styled buttons with radio semantics (mockup .preset)
					<button
						key={name}
						type="button"
						role="radio"
						aria-checked={on}
						aria-disabled={disabled || undefined}
						data-preset={name}
						tabIndex={i === focusIndex ? 0 : -1}
						onClick={() => (disabled ? undefined : onChange(name))}
						className={cn(
							"grid content-start gap-1.5 rounded-md border border-border bg-muted p-3.5 text-left transition-[border-color] duration-[var(--t)] hover:border-input",
							on && "border-primary bg-card shadow-[0_0_0_3px_var(--primary-soft)]",
						)}
					>
						<span className="flex items-center justify-between gap-2">
							<span className="font-display text-[15px] font-bold">{presetLabel(name)}</span>
							{current === name ? <Badge variant="info">current</Badge> : null}
						</span>
						<span className="text-[12.5px] text-foreground-2">{t.retrieval.presets[name]?.body ?? ""}</span>
						{top != null && fin != null ? (
							<span className="font-mono text-[11.5px] text-muted-foreground">
								{fill(t.retrieval.presetMeta, { top, final: fin })}
							</span>
						) : null}
					</button>
				);
			})}
		</div>
	);
}
