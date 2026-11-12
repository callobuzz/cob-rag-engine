import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { minorToInput, parseMajor } from "@/lib/format/money";
import { en, fill } from "@/lib/strings/en";
import { valueStrings as t } from "@/lib/strings/value";
import { useSaveValueSettings, type ValueSettings, type ValueSettingsBody } from "./api";

const s = t.settings;

/** `150000` → `"1500"`, `150050` → `"1500.50"`: what a person would type. */
export function toRupees(minor: number): string {
	const text = minorToInput(minor);
	return text.endsWith(".00") ? text.slice(0, -3) : text;
}

interface Draft {
	lead: string;
	handoff: string;
	staff: string;
	minutes: string;
	email: boolean;
	industry: string | null;
}

function draftFrom(v: ValueSettings): Draft {
	return {
		lead: toRupees(v.lead_value_minor),
		handoff: toRupees(v.handoff_value_minor),
		staff: toRupees(v.staff_hour_minor),
		minutes: String(v.minutes_per_answer),
		email: v.email_enabled,
		industry: v.industry_defaults,
	};
}

/**
 * "Change values" (owners): four numbers in rupees (converted to paise here, the only place a
 * person's text becomes money), the e-mail switch, and one tap to use the suggestions for this
 * kind of business. The engine's own limits (never negative, 0 to 120 minutes) are repeated here
 * so the message shows before sending.
 */
export function ValueSettingsDialog({
	open,
	onOpenChange,
	settings,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	settings: ValueSettings;
}) {
	const id = useId();
	const save = useSaveValueSettings();
	const [draft, setDraft] = useState<Draft>(() => draftFrom(settings));
	const [errors, setErrors] = useState<Partial<Record<"lead" | "handoff" | "staff" | "minutes", string>>>({});
	const [failure, setFailure] = useState<string | null>(null);

	useEffect(() => {
		if (!open) return;
		setDraft(draftFrom(settings));
		setErrors({});
		setFailure(null);
	}, [open, settings]);

	const edit = (patch: Partial<Draft>) => setDraft((d) => ({ ...d, ...patch, industry: null }));
	const kind = t.kinds[settings.business_type] ?? t.kinds.generic_support ?? "";

	const useSuggested = () => {
		const g = settings.suggested;
		setDraft((d) => ({
			...d,
			lead: toRupees(g.lead_value_minor),
			handoff: toRupees(g.handoff_value_minor),
			staff: toRupees(g.staff_hour_minor),
			minutes: String(g.minutes_per_answer),
			industry: settings.business_type,
		}));
		setErrors({});
	};

	const submit = async () => {
		setFailure(null);
		const lead = parseMajor(draft.lead);
		const handoff = parseMajor(draft.handoff);
		const staff = parseMajor(draft.staff);
		const minutes = /^\d{1,3}$/.test(draft.minutes.trim()) ? Number(draft.minutes.trim()) : null;
		const next: typeof errors = {};
		if (lead === null || lead < 0) next.lead = s.errors.amount;
		if (handoff === null || handoff < 0) next.handoff = s.errors.amount;
		if (staff === null || staff < 0) next.staff = s.errors.amount;
		if (minutes === null || minutes > 120) next.minutes = s.errors.minutes;
		setErrors(next);
		if (lead === null || handoff === null || staff === null || minutes === null || Object.keys(next).length > 0) return;
		try {
			await save.mutateAsync({
				lead_value_minor: lead,
				handoff_value_minor: handoff,
				staff_hour_minor: staff,
				minutes_per_answer: minutes,
				email_enabled: draft.email,
				industry_defaults: draft.industry as ValueSettingsBody["industry_defaults"],
			});
			toast.success(s.saved);
			onOpenChange(false);
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
		}
	};

	const money = (key: "lead" | "handoff" | "staff", label: string, hint: string) => (
		<Field id={`${id}-${key}`} label={label} hint={hint} error={errors[key]}>
			<Input
				id={`${id}-${key}`}
				inputMode="decimal"
				autoComplete="off"
				value={draft[key]}
				aria-invalid={errors[key] ? true : undefined}
				aria-describedby={errors[key] ? `${id}-${key}-error` : `${id}-${key}-hint`}
				onChange={(e) => edit({ [key]: e.target.value })}
			/>
		</Field>
	);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[520px]">
				<form
					noValidate
					className="grid gap-4"
					onSubmit={(e) => {
						e.preventDefault();
						void submit();
					}}
				>
					<DialogHeader>
						<DialogTitle>{s.title}</DialogTitle>
						<DialogDescription>
							{s.lead} {fill(s.currency, { currency: settings.currency === "USD" ? "dollars" : "rupees" })}
						</DialogDescription>
					</DialogHeader>
					{failure ? <FormAlert>{failure}</FormAlert> : null}
					<div className="grid gap-1">
						<Button type="button" variant="outline" size="sm" className="justify-self-start" onClick={useSuggested}>
							{fill(s.useSuggested, { kind })}
						</Button>
						<small className="text-[12.5px] text-muted-foreground">{s.suggestedNote}</small>
					</div>
					{money("lead", s.leadValue, s.leadHint)}
					{money("handoff", s.handoffValue, s.handoffHint)}
					{money("staff", s.staffHour, s.staffHint)}
					<Field id={`${id}-minutes`} label={s.minutes} hint={s.minutesHint} error={errors.minutes}>
						<Input
							id={`${id}-minutes`}
							inputMode="numeric"
							autoComplete="off"
							value={draft.minutes}
							aria-invalid={errors.minutes ? true : undefined}
							aria-describedby={errors.minutes ? `${id}-minutes-error` : `${id}-minutes-hint`}
							onChange={(e) => edit({ minutes: e.target.value })}
						/>
					</Field>
					<div className="flex items-center gap-2.5">
						<Switch
							id={`${id}-email`}
							checked={draft.email}
							onCheckedChange={(v) => setDraft((d) => ({ ...d, email: v }))}
						/>
						<label htmlFor={`${id}-email`} className="text-[13.5px] font-semibold">
							{s.emailToggle}
						</label>
					</div>
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={save.isPending} pendingLabel={s.saving}>
							{s.save}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
