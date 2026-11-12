import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { CopyButton } from "@/components/patterns/CopyButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { formatMinor, parseMajor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { paymentStrings } from "@/lib/strings/payments";
import { type PaymentSettings, paymentSettingsQuery, useSavePaymentSettings, useTestPaymentSettings } from "../api";

const t = paymentStrings.admin.settings;
const toRupees = (minor: number) => String(minor / 100);

/**
 * Billing → Payments: the payment service and its keys (P1). Keys are write-only: the page shows
 * whether each is saved, never the value. A key set in the server's environment wins and is said so.
 * The webhook address and the events to tick are right here, with a copy button.
 */
export function PaymentSettingsCard() {
	const q = useQuery(paymentSettingsQuery);
	if (q.isPending) return <CardSkeleton lines={5} />;
	if (q.isError) return <ErrorState error={q.error} title={t.title} onRetry={() => void q.refetch()} />;
	return <SettingsForm settings={q.data} />;
}

function SettingsForm({ settings }: { settings: PaymentSettings }) {
	const id = useId();
	const save = useSavePaymentSettings();
	const test = useTestPaymentSettings();
	const [provider, setProvider] = useState<"none" | "razorpay">(settings.provider);
	const [keyId, setKeyId] = useState(settings.key_id ?? "");
	const [secret, setSecret] = useState("");
	const [webhook, setWebhook] = useState("");
	const [clearSecret, setClearSecret] = useState(false);
	const [clearWebhook, setClearWebhook] = useState(false);
	const [minTopUp, setMinTopUp] = useState(toRupees(settings.min_top_up_minor));
	const [maxPayment, setMaxPayment] = useState(toRupees(settings.max_payment_minor));
	const [linkDays, setLinkDays] = useState(String(settings.link_expiry_days));
	const [error, setError] = useState<string | null>(null);
	const [testNote, setTestNote] = useState<{ ok: boolean; text: string } | null>(null);

	useEffect(() => {
		setProvider(settings.provider);
		setKeyId(settings.key_id ?? "");
		setMinTopUp(toRupees(settings.min_top_up_minor));
		setMaxPayment(toRupees(settings.max_payment_minor));
		setLinkDays(String(settings.link_expiry_days));
	}, [settings]);

	const submit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		const min = parseMajor(minTopUp);
		const max = parseMajor(maxPayment);
		const days = Number(linkDays);
		if (min === null || max === null || min < 100 || max < 100 || !Number.isInteger(days) || days < 1 || days > 30) {
			setError(t.rangeError);
			return;
		}
		if (min > max) {
			setError(t.rangeError);
			return;
		}
		try {
			await save.mutateAsync({
				provider,
				key_id: keyId.trim() === "" ? "" : keyId.trim(),
				key_secret: clearSecret ? "" : secret === "" ? null : secret,
				webhook_secret: clearWebhook ? "" : webhook === "" ? null : webhook,
				min_top_up_minor: min,
				max_payment_minor: max,
				link_expiry_days: days,
			});
			setSecret("");
			setWebhook("");
			setClearSecret(false);
			setClearWebhook(false);
			toast.success(t.savedToast);
		} catch (err) {
			const apiErr = toApiError(err);
			setError(isApiError(apiErr) && apiErr.code === "invalid_key_id" ? t.invalidKey : messageFor(apiErr).title);
		}
	};

	const runTest = async () => {
		setTestNote(null);
		try {
			const res = await test.mutateAsync();
			setTestNote({ ok: true, text: fill(t.testOk, { mode: t.mode[res.mode] }) });
		} catch (err) {
			const apiErr = toApiError(err);
			const reason = isApiError(apiErr) ? (apiErr.extra?.reason as string | undefined) : undefined;
			setTestNote({
				ok: false,
				text:
					reason === "auth"
						? t.testAuth
						: reason === "not_configured"
							? t.testNotConfigured
							: reason === "unreachable"
								? t.testUnreachable
								: messageFor(apiErr).title,
			});
		}
	};

	return (
		<form onSubmit={(e) => void submit(e)} noValidate data-testid="payment-settings">
			<Panel
				title={t.title}
				sub={t.lead}
				aside={
					<div className="flex items-center gap-2">
						{settings.mode ? (
							<Badge variant={settings.mode === "live" ? "ok" : "warn"}>{t.mode[settings.mode]}</Badge>
						) : null}
						<Badge variant={settings.enabled ? "ok" : "default"}>{settings.enabled ? t.enabled : t.disabled}</Badge>
					</div>
				}
			>
				<div className="grid gap-4 p-4">
					{error ? <FormAlert>{error}</FormAlert> : null}
					{settings.source === "env" ? <FormAlert tone="info">{t.source.env}</FormAlert> : null}
					<ol className="m-0 grid list-decimal gap-1 pl-5 text-[13px] text-foreground-2" data-testid="payment-steps">
						{t.steps.map((step) => (
							<li key={step}>{step}</li>
						))}
					</ol>
					<div className="grid gap-4 min-[700px]:grid-cols-2">
						<Field id={`${id}-provider`} label={t.provider}>
							<NativeSelect
								id={`${id}-provider`}
								value={provider}
								onChange={(e) => setProvider(e.target.value as "none" | "razorpay")}
							>
								<option value="none">{t.off}</option>
								<option value="razorpay">{t.razorpay}</option>
							</NativeSelect>
						</Field>
						<Field id={`${id}-keyid`} label={t.keyId} hint={t.keyIdHint}>
							<Input
								id={`${id}-keyid`}
								autoComplete="off"
								className="font-mono"
								value={keyId}
								onChange={(e) => setKeyId(e.target.value)}
							/>
						</Field>
						<Field
							id={`${id}-secret`}
							label={t.keySecret}
							labelExtra={
								<Badge variant={settings.key_secret_set && !clearSecret ? "ok" : "default"}>
									{settings.key_secret_set && !clearSecret ? t.saved : t.notSaved}
								</Badge>
							}
						>
							<Input
								id={`${id}-secret`}
								type="password"
								autoComplete="new-password"
								placeholder={settings.key_secret_set ? t.secretPlaceholder : ""}
								value={secret}
								onChange={(e) => setSecret(e.target.value)}
							/>
							{settings.key_secret_set ? (
								<label className="flex items-center gap-2 text-[12.5px]">
									<input type="checkbox" checked={clearSecret} onChange={(e) => setClearSecret(e.target.checked)} />
									{t.clear}
								</label>
							) : null}
						</Field>
						<Field
							id={`${id}-webhook`}
							label={t.webhookSecret}
							labelExtra={
								<Badge variant={settings.webhook_secret_set && !clearWebhook ? "ok" : "default"}>
									{settings.webhook_secret_set && !clearWebhook ? t.saved : t.notSaved}
								</Badge>
							}
						>
							<Input
								id={`${id}-webhook`}
								type="password"
								autoComplete="new-password"
								placeholder={settings.webhook_secret_set ? t.secretPlaceholder : ""}
								value={webhook}
								onChange={(e) => setWebhook(e.target.value)}
							/>
							{settings.webhook_secret_set ? (
								<label className="flex items-center gap-2 text-[12.5px]">
									<input type="checkbox" checked={clearWebhook} onChange={(e) => setClearWebhook(e.target.checked)} />
									{t.clear}
								</label>
							) : null}
						</Field>
					</div>
					<div className="grid gap-2" data-testid="payment-webhook-url">
						<div className="text-[13px] font-semibold">{t.webhookUrl}</div>
						<div className="flex flex-wrap items-center gap-2">
							<code className="min-w-0 break-all rounded-sm bg-muted px-2 py-1 text-[12.5px]">
								{settings.webhook_url}
							</code>
							<CopyButton value={settings.webhook_url} />
						</div>
						<p className="m-0 text-[12.5px] text-muted-foreground">{t.webhookHelp}</p>
						<div className="text-[12.5px]">
							<span className="font-semibold">{t.events}: </span>
							{settings.events.join(", ")}
						</div>
						<p className="m-0 text-[12.5px] text-muted-foreground">{t.captureNote}</p>
					</div>
					<fieldset className="m-0 grid gap-3 border-0 p-0 min-[700px]:grid-cols-3">
						<legend className="mb-2 text-[13px] font-semibold">{t.limits}</legend>
						<Field id={`${id}-min`} label={t.minTopUp} hint={formatMinor(settings.min_top_up_minor, "INR")}>
							<Input
								id={`${id}-min`}
								inputMode="decimal"
								value={minTopUp}
								onChange={(e) => setMinTopUp(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-max`} label={t.maxPayment} hint={formatMinor(settings.max_payment_minor, "INR")}>
							<Input
								id={`${id}-max`}
								inputMode="decimal"
								value={maxPayment}
								onChange={(e) => setMaxPayment(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-days`} label={t.linkDays}>
							<Input
								id={`${id}-days`}
								inputMode="numeric"
								value={linkDays}
								onChange={(e) => setLinkDays(e.target.value)}
							/>
						</Field>
					</fieldset>
					{testNote ? (
						<FormAlert tone={testNote.ok ? "info" : "bad"}>
							<span data-testid="payment-test-result">{testNote.text}</span>
						</FormAlert>
					) : null}
				</div>
				<FormFoot>
					<Button type="button" variant="outline" disabled={test.isPending} onClick={() => void runTest()}>
						{test.isPending ? t.testing : t.test}
					</Button>
					<SubmitButton variant="default" pending={save.isPending} pendingLabel={t.saving}>
						{t.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
