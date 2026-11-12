import { useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
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
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { type ManualConnect, useManualConnect } from "./api";

const DIGITS = /^\d{5,20}$/;

/** What is wrong with the form (empty = nothing): ids are digits, the token is pasted. */
export function manualProblems(v: {
	phone: string;
	waba: string;
	token: string;
	pin?: string;
}): Partial<Record<"phone" | "waba" | "token" | "pin", string>> {
	const out: Partial<Record<"phone" | "waba" | "token" | "pin", string>> = {};
	if (v.pin?.trim() && !/^\d{6}$/.test(v.pin.trim())) out.pin = t.admin.pinError;
	if (!DIGITS.test(v.phone.trim())) out.phone = t.admin.idError;
	if (!DIGITS.test(v.waba.trim())) out.waba = t.admin.idError;
	if (v.token.trim().length < 10) out.token = t.admin.tokenError;
	return out;
}

/**
 * The deployer connects a number by hand (TV6): phone number id, WABA id, a permanent token from
 * the deployer's own Meta app, and who pays Meta. The token is checked by the engine, stored
 * encrypted and never shown again.
 */
export function ManualConnectDialog({
	kb,
	kbName,
	open,
	onOpenChange,
}: {
	kb: string;
	kbName: string;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[520px]">
				{open ? <ManualForm kb={kb} kbName={kbName} onDone={() => onOpenChange(false)} /> : null}
			</DialogContent>
		</Dialog>
	);
}

function ManualForm({ kb, kbName, onDone }: { kb: string; kbName: string; onDone: () => void }) {
	const id = useId();
	const connect = useManualConnect(kb);
	const [phone, setPhone] = useState("");
	const [waba, setWaba] = useState("");
	const [token, setToken] = useState("");
	const [pin, setPin] = useState("");
	const [billing, setBilling] = useState<ManualConnect["meta_billing"]>("deployer");
	const [shown, setShown] = useState(false);
	const [failure, setFailure] = useState<string | null>(null);
	const problems = manualProblems({ phone, waba, token, pin });

	const submit = (e: React.FormEvent) => {
		e.preventDefault();
		setShown(true);
		setFailure(null);
		if (Object.keys(problems).length > 0) return;
		connect.mutate(
			{
				phone_number_id: phone.trim(),
				waba_id: waba.trim(),
				access_token: token.trim(),
				meta_billing: billing,
				...(pin.trim() ? { pin: pin.trim() } : {}),
			},
			{
				onSuccess: () => {
					toast.success(t.admin.done);
					onDone();
				},
				onError: (err) => {
					const e2 = toApiError(err);
					const known: Record<string, string> = {
						whatsapp_token_invalid: t.admin.tokenRejected,
						whatsapp_number_in_use: t.admin.inUse,
						whatsapp_not_in_plan: t.admin.notInPlan,
						whatsapp_not_enabled: t.admin.notEnabled,
					};
					const registerStep = e2.code === "whatsapp_signup_failed" && e2.extra.step === "register";
					setFailure(
						(registerStep ? t.admin.registerFailed : undefined) || (e2.code && known[e2.code]) || messageFor(e2).title,
					);
				},
			},
		);
	};

	return (
		<form onSubmit={submit} noValidate data-testid="wa-manual-form" className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{t.admin.dialogTitle}</DialogTitle>
				<DialogDescription>
					{kbName} · {t.admin.dialogLead}
				</DialogDescription>
			</DialogHeader>
			{failure ? <FormAlert>{failure}</FormAlert> : null}
			<Field
				id={`${id}-phone`}
				label={t.admin.phoneNumberId}
				hint={t.admin.phoneNumberIdHint}
				error={shown ? problems.phone : null}
			>
				<Input
					id={`${id}-phone`}
					inputMode="numeric"
					autoComplete="off"
					value={phone}
					aria-invalid={shown && problems.phone ? true : undefined}
					aria-describedby={describedBy(`${id}-phone`, { hint: true, error: shown && !!problems.phone })}
					onChange={(e) => setPhone(e.target.value)}
				/>
			</Field>
			<Field id={`${id}-waba`} label={t.admin.wabaId} hint={t.admin.wabaIdHint} error={shown ? problems.waba : null}>
				<Input
					id={`${id}-waba`}
					inputMode="numeric"
					autoComplete="off"
					value={waba}
					aria-invalid={shown && problems.waba ? true : undefined}
					aria-describedby={describedBy(`${id}-waba`, { hint: true, error: shown && !!problems.waba })}
					onChange={(e) => setWaba(e.target.value)}
				/>
			</Field>
			<Field id={`${id}-token`} label={t.admin.token} hint={t.admin.tokenHint} error={shown ? problems.token : null}>
				<Input
					id={`${id}-token`}
					type="password"
					autoComplete="off"
					spellCheck={false}
					value={token}
					aria-invalid={shown && problems.token ? true : undefined}
					aria-describedby={describedBy(`${id}-token`, { hint: true, error: shown && !!problems.token })}
					onChange={(e) => setToken(e.target.value)}
				/>
			</Field>
			<Field id={`${id}-pin`} label={t.admin.pin} hint={t.admin.pinHint} error={shown ? problems.pin : null}>
				<Input
					id={`${id}-pin`}
					inputMode="numeric"
					autoComplete="off"
					maxLength={6}
					value={pin}
					aria-invalid={shown && problems.pin ? true : undefined}
					aria-describedby={describedBy(`${id}-pin`, { hint: true, error: shown && !!problems.pin })}
					onChange={(e) => setPin(e.target.value)}
				/>
			</Field>
			<Field id={`${id}-billing`} label={t.admin.billing}>
				<NativeSelect
					id={`${id}-billing`}
					value={billing}
					onChange={(e) => setBilling(e.target.value as ManualConnect["meta_billing"])}
				>
					<option value="deployer">{t.admin.billingDeployer}</option>
					<option value="business">{t.admin.billingBusiness}</option>
				</NativeSelect>
			</Field>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					Cancel
				</Button>
				<SubmitButton variant="default" pending={connect.isPending} pendingLabel={t.admin.submitting}>
					{t.admin.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
