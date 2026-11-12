import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { parseMajor } from "@/lib/format/money";
import { noSurprisesStrings } from "@/lib/strings/no-surprises";
import { type Settings, useSaveSettings } from "./api";

const s = noSurprisesStrings.settings;

type Trust = NonNullable<Settings["trust"]>;

function wholeNumber(text: string, min: number, max: number): number | null {
	if (!/^\d{1,3}$/.test(text.trim())) return null;
	const n = Number(text);
	return n >= min && n <= max ? n : null;
}

/** A cap ceiling typed in rupees or dollars → minor units; empty means no ceiling (null). */
function maxFromText(text: string): number | null | "invalid" {
	if (text.trim() === "") return null;
	const minor = parseMajor(text);
	return minor == null || minor < 1 ? "invalid" : minor;
}

/**
 * TV3: what a provider tells its businesses before it charges them, the highest spending cap an
 * owner may set, and whether (and how fast) the provider answers support requests. One form,
 * one save (`PATCH /admin/settings` with only the `trust` fields it owns).
 */
export function NoticesCard({ trust }: { trust: Trust | undefined }) {
	const id = useId();
	const save = useSaveSettings();
	const [renewal, setRenewal] = useState(String(trust?.renewal_reminder_days ?? 7));
	const [notice, setNotice] = useState(String(trust?.price_change_notice_days ?? 30));
	const [inr, setInr] = useState(
		trust?.spend_cap_max_minor?.INR != null ? String(trust.spend_cap_max_minor.INR / 100) : "",
	);
	const [usd, setUsd] = useState(
		trust?.spend_cap_max_minor?.USD != null ? String(trust.spend_cap_max_minor.USD / 100) : "",
	);
	const [supportOn, setSupportOn] = useState(trust?.support_enabled !== false);
	const [sla, setSla] = useState(trust?.support_sla_hours != null ? String(trust.support_sla_hours) : "");
	const [error, setError] = useState<string | null>(null);

	const submit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		const renewalDays = wholeNumber(renewal, 0, 30);
		const noticeDays = wholeNumber(notice, 7, 90);
		const slaHours = sla.trim() === "" ? null : wholeNumber(sla, 1, 168);
		const maxInr = maxFromText(inr);
		const maxUsd = maxFromText(usd);
		if (
			renewalDays == null ||
			noticeDays == null ||
			(sla.trim() !== "" && slaHours == null) ||
			maxInr === "invalid" ||
			maxUsd === "invalid"
		) {
			return setError(s.invalid);
		}
		const caps: Record<string, number> = {};
		if (maxInr != null) caps.INR = maxInr;
		if (maxUsd != null) caps.USD = maxUsd;
		try {
			await save.mutateAsync({
				trust: {
					renewal_reminder_days: renewalDays,
					price_change_notice_days: noticeDays,
					spend_cap_max_minor: caps,
					support_enabled: supportOn,
					support_sla_hours: slaHours,
				},
			});
			toast.success(s.saved);
		} catch (err) {
			setError(messageFor(toApiError(err)).title);
		}
	};

	return (
		<form onSubmit={(e) => void submit(e)} noValidate data-testid="notices-card">
			<Panel title={s.title} sub={s.sub}>
				<div className="grid gap-4 p-4">
					{error ? <FormAlert>{error}</FormAlert> : null}
					<div className="grid gap-4 min-[600px]:grid-cols-2">
						<Field id={`${id}-renewal`} label={s.renewalDays} hint={s.renewalDaysHint}>
							<Input
								id={`${id}-renewal`}
								inputMode="numeric"
								value={renewal}
								aria-describedby={`${id}-renewal-hint`}
								onChange={(e) => setRenewal(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-notice`} label={s.noticeDays} hint={s.noticeDaysHint}>
							<Input
								id={`${id}-notice`}
								inputMode="numeric"
								value={notice}
								aria-describedby={`${id}-notice-hint`}
								onChange={(e) => setNotice(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-inr`} label={s.capMaxInr} hint={s.capMaxHint}>
							<Input
								id={`${id}-inr`}
								inputMode="decimal"
								value={inr}
								aria-describedby={`${id}-inr-hint`}
								onChange={(e) => setInr(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-usd`} label={s.capMaxUsd} hint={s.capMaxHint}>
							<Input
								id={`${id}-usd`}
								inputMode="decimal"
								value={usd}
								aria-describedby={`${id}-usd-hint`}
								onChange={(e) => setUsd(e.target.value)}
							/>
						</Field>
					</div>
					<div className="grid gap-1">
						<div className="flex items-center gap-2.5">
							<Switch id={`${id}-support`} checked={supportOn} onCheckedChange={setSupportOn} />
							<label htmlFor={`${id}-support`} className="text-[13.5px] font-semibold">
								{s.supportOn}
							</label>
						</div>
						<small className="pl-[42px] text-[12.5px] text-muted-foreground">{s.supportOnHint}</small>
					</div>
					<Field id={`${id}-sla`} label={s.sla} hint={s.slaHint}>
						<Input
							id={`${id}-sla`}
							inputMode="numeric"
							value={sla}
							aria-describedby={`${id}-sla-hint`}
							onChange={(e) => setSla(e.target.value)}
						/>
					</Field>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={save.isPending} pendingLabel={s.saving}>
						{s.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
