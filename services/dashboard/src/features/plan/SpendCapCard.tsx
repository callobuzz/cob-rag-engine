import { useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { formatWhole } from "@/features/value/format";
import type { OwnBillingView } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { type Currency, minorToInput, parseMajor } from "@/lib/format/money";
import { en, fill } from "@/lib/strings/en";
import { noSurprisesStrings } from "@/lib/strings/no-surprises";
import { cn } from "@/lib/utils";
import { Panel } from "../kbs/ui";
import { useSetSpendCap } from "./api";

const s = noSurprisesStrings.cap;

/** The cap a person typed ("2,000") as minor units, or the sentence that says what is wrong. */
export function capFromText(
	text: string,
	currency: Currency,
	maxMinor: number | null | undefined,
	product: string,
): { minor: number } | { error: string } {
	const minor = parseMajor(text, currency);
	if (minor == null) return { error: s.errors.amount };
	if (minor <= 0) return { error: s.errors.zero };
	if (maxMinor != null && minor > maxMinor) {
		return { error: fill(s.errors.tooHigh, { product, max: formatWhole(maxMinor, currency) }) };
	}
	return { minor };
}

/**
 * The owner's spending cap (TV3): this month's usage against it, what happens at the cap, and
 * the form to set or remove it. Members see it read-only. Not shown for a plan that charges nothing.
 */
export function SpendCapCard({ view }: { view: OwnBillingView }) {
	const id = useId();
	const principal = usePrincipal();
	const { productName } = useBranding();
	const save = useSetSpendCap();
	const cap = view.spend_cap;
	const [editing, setEditing] = useState(false);
	const [text, setText] = useState("");
	const [error, setError] = useState<string | null>(null);
	if (!cap) return null;
	const owner = principal?.role === "owner" || principal?.auth === "api_key";
	const currency = view.currency as Currency;
	const share = cap.cap_minor ? Math.min(1, cap.used_minor / cap.cap_minor) : 0;

	const startEdit = () => {
		// keep the paise: saving a cap unchanged must not lower it
		setText(
			cap.cap_minor == null
				? ""
				: cap.cap_minor % 100 === 0
					? String(cap.cap_minor / 100)
					: minorToInput(cap.cap_minor),
		);
		setError(null);
		setEditing(true);
	};
	const submit = async (next: number | null) => {
		setError(null);
		try {
			await save.mutateAsync(next);
			toast.success(next == null ? s.removed : s.saved);
			setEditing(false);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	const onSubmit = (e: React.FormEvent) => {
		e.preventDefault();
		if (text.trim() === "") return void submit(null);
		const parsed = capFromText(text, currency, cap.max_minor, productName);
		if ("error" in parsed) return setError(parsed.error);
		void submit(parsed.minor);
	};

	return (
		<Panel title={s.title} sub={s.lead}>
			<div data-testid="spend-cap-card" className="grid gap-3 p-4">
				<p data-testid="spend-cap-line" className="m-0 text-[14.5px] font-semibold">
					{cap.cap_minor != null
						? fill(s.used, { used: formatWhole(cap.used_minor, currency), cap: formatWhole(cap.cap_minor, currency) })
						: fill(s.usedNoCap, { used: formatWhole(cap.used_minor, currency) })}
				</p>
				{cap.cap_minor != null ? (
					<div
						role="progressbar"
						aria-label={s.title}
						aria-valuemin={0}
						aria-valuemax={100}
						aria-valuenow={Math.round(share * 100)}
						className="h-1.5 overflow-hidden rounded-[3px] bg-secondary"
					>
						<i
							className={cn(
								"block h-full rounded-[3px]",
								cap.state === "reached" ? "bg-destructive" : cap.state === "near" ? "bg-warning" : "bg-primary",
							)}
							style={{ width: `${Math.round(share * 1000) / 10}%` }}
						/>
					</div>
				) : (
					<p className="m-0 text-[13px] text-muted-foreground">{s.none}</p>
				)}
				{cap.state === "near" ? (
					<p role="status" data-testid="spend-cap-state" className="m-0 text-[13px]">
						{s.near}
					</p>
				) : cap.state === "reached" ? (
					<p role="status" data-testid="spend-cap-state" className="m-0 text-[13px] text-destructive">
						{s.reached}
					</p>
				) : null}
				{cap.cap_minor != null ? (
					<small className="text-[12.5px] text-muted-foreground">{fill(s.atCap, { product: productName })}</small>
				) : null}
				{cap.max_minor != null ? (
					<small className="text-[12.5px] text-muted-foreground">
						{fill(s.max, { product: productName, max: formatWhole(cap.max_minor, currency) })}
					</small>
				) : null}
				{owner ? (
					editing ? (
						<form onSubmit={onSubmit} noValidate className="grid gap-3">
							{error ? <FormAlert id={`${id}-error`}>{error}</FormAlert> : null}
							<Field id={id} label={currency === "USD" ? s.fieldUsd : s.field} hint={s.hint}>
								<Input
									id={id}
									inputMode="decimal"
									value={text}
									onChange={(e) => setText(e.target.value)}
									aria-invalid={error ? true : undefined}
									aria-describedby={describedBy(id, { hint: true, error: !!error })}
								/>
							</Field>
							<div className="flex flex-wrap gap-2">
								<SubmitButton variant="default" pending={save.isPending} pendingLabel={s.saving}>
									{s.save}
								</SubmitButton>
								<Button type="button" variant="ghost" onClick={() => setEditing(false)}>
									{en.common.cancel}
								</Button>
							</div>
						</form>
					) : (
						<div className="flex flex-wrap gap-2">
							<Button type="button" variant="outline" size="sm" data-testid="spend-cap-edit" onClick={startEdit}>
								{cap.cap_minor != null ? s.change : s.set}
							</Button>
							{cap.cap_minor != null ? (
								<Button
									type="button"
									variant="ghost"
									size="sm"
									data-testid="spend-cap-remove"
									disabled={save.isPending}
									onClick={() => void submit(null)}
								>
									{s.remove}
								</Button>
							) : null}
						</div>
					)
				) : (
					<small className="text-[12.5px] text-muted-foreground">{s.ownerOnly}</small>
				)}
			</div>
		</Panel>
	);
}
