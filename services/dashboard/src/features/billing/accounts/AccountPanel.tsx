import { useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { FormFoot, Panel } from "@/features/kbs/ui";
import type { BillingAccount } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { customerBillingStrings as t } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";
import { type UpdateAccountInput, useUpdateBillingAccount } from "../api";

type Pause = BillingAccount["pause_override"];

/**
 * Currency (until the first transaction), billing day, pausing and notes. Pausing a customer now
 * asks first. Notes are only ever shown here, never to the customer.
 */
export function AccountPanel({ account }: { account: BillingAccount }) {
	const ids = { currency: useId(), day: useId(), pause: useId(), notes: useId() };
	const update = useUpdateBillingAccount(account.tenant_id);
	const [currency, setCurrency] = useState(account.currency);
	const [day, setDay] = useState(String(account.billing_day));
	const [pause, setPause] = useState<Pause>(account.pause_override);
	const [notes, setNotes] = useState(account.notes ?? "");
	const [errors, setErrors] = useState<{ day?: string; notes?: string }>({});
	const [failure, setFailure] = useState<string | null>(null);
	const [confirming, setConfirming] = useState<UpdateAccountInput | null>(null);

	const send = async (body: UpdateAccountInput) => {
		setFailure(null);
		try {
			await update.mutateAsync(body);
			toast.success(t.account.saved, { description: fill(t.account.savedBody, { name: account.tenant_name }) });
			setConfirming(null);
		} catch (e) {
			setConfirming(null);
			setFailure(messageFor(toApiError(e)).title);
		}
	};

	const submit = () => {
		const found: { day?: string; notes?: string } = {};
		const dayNumber = /^\d{1,2}$/.test(day.trim()) ? Number(day.trim()) : Number.NaN;
		if (!(dayNumber >= 1 && dayNumber <= 28)) found.day = t.account.billingDayInvalid;
		if (notes.length > 2000) found.notes = t.account.notesLong;
		setErrors(found);
		if (found.day || found.notes) return;
		const body: UpdateAccountInput = {};
		if (currency !== account.currency) body.currency = currency;
		if (dayNumber !== account.billing_day) body.billing_day = dayNumber;
		if (pause !== account.pause_override) body.pause_override = pause;
		if (notes.trim() !== (account.notes ?? "")) body.notes = notes.trim();
		if (Object.keys(body).length === 0) {
			toast.info(t.terms.nothing);
			return;
		}
		if (body.pause_override === "force") setConfirming(body);
		else void send(body);
	};

	return (
		<>
			<form
				onSubmit={(e) => {
					e.preventDefault();
					submit();
				}}
				noValidate
			>
				<Panel title={t.account.title}>
					<div className="grid gap-4 p-4">
						{failure ? <FormAlert>{failure}</FormAlert> : null}
						<div className="grid gap-4 min-[861px]:grid-cols-2">
							<Field
								id={ids.currency}
								label={t.account.currency}
								hint={account.currency_locked ? t.account.currencyLocked : t.account.currencyHint}
							>
								<NativeSelect
									id={ids.currency}
									value={currency}
									disabled={account.currency_locked}
									aria-describedby={describedBy(ids.currency, { hint: true })}
									onChange={(e) => setCurrency(e.target.value as typeof currency)}
								>
									<option value="INR">INR (₹)</option>
									<option value="USD">USD ($)</option>
								</NativeSelect>
							</Field>
							<Field id={ids.day} label={t.account.billingDay} hint={t.account.billingDayHint} error={errors.day}>
								<Input
									id={ids.day}
									inputMode="numeric"
									autoComplete="off"
									className="font-mono"
									value={day}
									aria-invalid={!!errors.day || undefined}
									aria-describedby={describedBy(ids.day, { hint: true, error: !!errors.day })}
									onChange={(e) => {
										setDay(e.target.value);
										setErrors((x) => ({ ...x, day: undefined }));
									}}
								/>
							</Field>
						</div>
						<Field id={ids.pause} label={t.account.pause} hint={t.account.pauseHint}>
							<NativeSelect
								id={ids.pause}
								value={pause}
								aria-describedby={describedBy(ids.pause, { hint: true })}
								onChange={(e) => setPause(e.target.value as Pause)}
							>
								<option value="auto">{t.account.pauseOptions.auto}</option>
								<option value="never">{t.account.pauseOptions.never}</option>
								<option value="force">{t.account.pauseOptions.force}</option>
							</NativeSelect>
						</Field>
						<Field id={ids.notes} label={t.account.notes} hint={t.account.notesHint} error={errors.notes}>
							<Textarea
								id={ids.notes}
								rows={3}
								value={notes}
								aria-invalid={!!errors.notes || undefined}
								aria-describedby={describedBy(ids.notes, { hint: true, error: !!errors.notes })}
								onChange={(e) => {
									setNotes(e.target.value);
									setErrors((x) => ({ ...x, notes: undefined }));
								}}
							/>
						</Field>
					</div>
					<FormFoot>
						<SubmitButton variant="default" pending={update.isPending} pendingLabel={t.account.saving}>
							{t.account.save}
						</SubmitButton>
					</FormFoot>
				</Panel>
			</form>
			<ConfirmDialog
				open={confirming !== null}
				onOpenChange={(o) => (o ? undefined : setConfirming(null))}
				title={fill(t.account.forceTitle, { name: account.tenant_name })}
				body={t.account.forceBody}
				confirmLabel={t.account.forceConfirm}
				pending={update.isPending}
				onConfirm={() => confirming && void send(confirming)}
			/>
		</>
	);
}
