import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Info } from "lucide-react";
import { type KeyboardEvent, useId, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { plansQuery } from "@/features/plans/api";
import { LIMIT_KEYS } from "@/features/plans/limits";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { brandingStrings } from "@/lib/strings/admin";
import { cobPublicStrings } from "@/lib/strings/cob-public";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { type SignupMode, useSaveSettings } from "./api";

const t = brandingStrings.mode;
export const MODES: SignupMode[] = ["open", "approval", "closed"];

/**
 * Mockup `#modes`: three cards as a radio group (arrow keys move the choice) with the note for
 * the chosen mode. Save sends only `{signup_mode}`. Open warns which plan new sign-ups start on,
 * and more loudly when that plan has no limits.
 */
export function SignupModeCard({
	saved,
	value,
	onChange,
	cobPublic = true,
}: {
	saved: SignupMode;
	value: SignupMode;
	onChange: (mode: SignupMode) => void;
	/** The saved "Ask COB on the sign-in pages" setting. */
	cobPublic?: boolean;
}) {
	const save = useSaveSettings();
	const [error, setError] = useState<string | null>(null);
	const focusIndex = Math.max(0, MODES.indexOf(value));

	// Picking another mode makes a shown save error stale: clear it.
	const choose = (mode: SignupMode) => {
		setError(null);
		onChange(mode);
	};

	const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
		if (!["ArrowRight", "ArrowLeft", "ArrowDown", "ArrowUp"].includes(e.key)) return;
		e.preventDefault();
		const dir = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : MODES.length - 1;
		const next = MODES[(focusIndex + dir) % MODES.length];
		if (!next) return;
		choose(next);
		e.currentTarget.querySelector<HTMLButtonElement>(`[data-mode="${next}"]`)?.focus();
	};

	const submit = async () => {
		setError(null);
		try {
			await save.mutateAsync({ signup_mode: value });
			toast.success(t.saved, { description: fill(t.savedBody, { mode: t.cards[value].name }) });
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};

	return (
		<Panel title={t.title} sub={t.sub}>
			<div className="grid gap-3 p-4">
				<div role="radiogroup" aria-label={t.label} onKeyDown={onKey} className="grid gap-2.5 min-[861px]:grid-cols-3">
					{MODES.map((m, i) => {
						const on = value === m;
						const c = t.cards[m];
						return (
							// biome-ignore lint/a11y/useSemanticElements: card-styled buttons with radio semantics (mockup .preset)
							<button
								key={m}
								type="button"
								role="radio"
								aria-checked={on}
								data-mode={m}
								tabIndex={i === focusIndex ? 0 : -1}
								onClick={() => choose(m)}
								className={cn(
									"grid content-start gap-1.5 rounded-md border border-border bg-muted p-3.5 text-left transition-[border-color] duration-[var(--t)] hover:border-input",
									on && "border-primary bg-card shadow-[0_0_0_3px_var(--primary-soft)]",
								)}
							>
								<span className="font-display text-[15px] font-bold">{c.name}</span>
								<span className="text-[12.5px] text-foreground-2">{c.body}</span>
								<span className="font-mono text-[11.5px] text-muted-foreground">{c.meta}</span>
							</button>
						);
					})}
				</div>
				<p className="m-0 flex items-start gap-2 text-[13px] text-foreground-2">
					<Info className="mt-0.5 size-4 shrink-0 text-muted-foreground" aria-hidden />
					<span data-testid="mode-note">{t.notes[value]}</span>
				</p>
				{value === "open" ? <OpenWarning /> : null}
				{error ? <FormAlert>{error}</FormAlert> : null}
				<AskCobSwitch checked={cobPublic} />
			</div>
			<FormFoot>
				<Button variant="default" disabled={value === saved || save.isPending} onClick={() => void submit()}>
					{save.isPending ? t.saving : t.save}
				</Button>
			</FormFoot>
		</Panel>
	);
}

/** COB6: show "Ask COB" on the sign-in pages. Saves at once; the engine's own switches still apply. */
function AskCobSwitch({ checked }: { checked: boolean }) {
	const id = useId();
	const save = useSaveSettings();
	const s = cobPublicStrings.switch;
	const [error, setError] = useState<string | null>(null);
	const toggle = async (next: boolean) => {
		setError(null);
		try {
			await save.mutateAsync({ cob_public_sign_in: next });
			toast.success(next ? s.savedOn : s.savedOff);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	return (
		<div className="grid gap-1 border-t border-border pt-3">
			<div className="flex items-center gap-2.5">
				<Switch
					id={id}
					checked={checked}
					disabled={save.isPending}
					aria-describedby={`${id}-hint`}
					onCheckedChange={(v) => void toggle(v)}
				/>
				<label htmlFor={id} className="text-[13.5px] font-semibold">
					{s.label}
				</label>
			</div>
			<small id={`${id}-hint`} className="pl-[42px] text-[12.5px] text-muted-foreground">
				{s.help}
			</small>
			{error ? <FormAlert>{error}</FormAlert> : null}
		</div>
	);
}

function OpenWarning() {
	const plans = useQuery(plansQuery);
	if (!plans.data) return null;
	const def = plans.data.items.find((p) => p.is_default);
	const unlimited = def ? LIMIT_KEYS.every((k) => def[k] == null) : true;
	return (
		<FormAlert tone="warn">
			{def ? fill(t.openPlan, { plan: def.name }) : null}{" "}
			{def && unlimited ? fill(t.openUnlimited, { plan: def.name }) : null}
			{def ? null : t.openNoPlan}{" "}
			<Link to="/admin/plans" className="font-semibold text-brand-text">
				{t.plansLink}
			</Link>
		</FormAlert>
	);
}
