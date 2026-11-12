import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";
import { cn } from "@/lib/utils";
import { FONT_PAIRINGS, type PairingKey } from "./fontPairings";

/** Six cards in a radio group: the pairing's name, how it feels, and which families it uses. */
export function FontPicker({
	value,
	onChange,
	disabled = false,
}: {
	value: PairingKey;
	onChange: (key: PairingKey) => void;
	disabled?: boolean;
}) {
	return (
		<fieldset className="m-0 grid min-w-0 gap-2 border-0 p-0" data-testid="font-picker" disabled={disabled}>
			<legend className="mb-2 text-[13px] font-semibold">{t.look.fonts}</legend>
			<div className="grid gap-2 min-[700px]:grid-cols-2 min-[1100px]:grid-cols-3">
				{FONT_PAIRINGS.map((p) => (
					<label
						key={p.key}
						className={cn(
							"grid cursor-pointer gap-0.5 rounded-sm border bg-card p-3 has-[:focus-visible]:ring-[3px] has-[:focus-visible]:ring-ring/50",
							value === p.key ? "border-primary bg-primary-soft" : "border-border hover:border-foreground-2",
							disabled && "cursor-not-allowed opacity-60",
						)}
					>
						<input
							type="radio"
							name="landing-font"
							value={p.key}
							checked={value === p.key}
							onChange={() => onChange(p.key)}
							className="sr-only"
							aria-label={fill(t.look.fontOption, { name: p.name, feel: p.feel })}
						/>
						<strong className="text-[14px]">{p.name}</strong>
						<span className="text-[13px] text-foreground-2">{p.feel}</span>
						<span className="text-[12.5px] text-muted-foreground">{p.families}</span>
					</label>
				))}
			</div>
		</fieldset>
	);
}
