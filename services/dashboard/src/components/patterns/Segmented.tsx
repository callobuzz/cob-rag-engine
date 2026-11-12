import type { KeyboardEvent, ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Mockup `.seg`: a radio group drawn as segmented buttons (arrow keys move the choice). */
export function Segmented<T extends string>({
	label,
	value,
	options,
	onChange,
}: {
	label: string;
	value: T;
	options: { value: T; label: ReactNode }[];
	onChange: (value: T) => void;
}) {
	const index = options.findIndex((o) => o.value === value);
	const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
		if (e.key !== "ArrowRight" && e.key !== "ArrowLeft") return;
		e.preventDefault();
		const next = options[(index + (e.key === "ArrowRight" ? 1 : options.length - 1)) % options.length];
		if (next) {
			onChange(next.value);
			const el = e.currentTarget.querySelector<HTMLButtonElement>(`[data-value="${next.value}"]`);
			el?.focus();
		}
	};
	return (
		<div
			role="radiogroup"
			aria-label={label}
			onKeyDown={onKey}
			className="inline-flex gap-0.5 rounded-[8px] border border-border bg-secondary p-[3px]"
		>
			{options.map((o) => {
				const on = o.value === value;
				return (
					// biome-ignore lint/a11y/useSemanticElements: segmented control styled buttons with radio semantics
					<button
						key={o.value}
						type="button"
						role="radio"
						aria-checked={on}
						data-value={o.value}
						tabIndex={on ? 0 : -1}
						onClick={() => onChange(o.value)}
						className={cn(
							"inline-flex h-7 items-center gap-1.5 rounded-[6px] px-2.5 text-[12.5px] font-semibold text-foreground-2",
							on && "bg-card text-foreground shadow-[0_1px_2px_rgba(0,0,0,.2)]",
						)}
					>
						{o.label}
					</button>
				);
			})}
		</div>
	);
}
