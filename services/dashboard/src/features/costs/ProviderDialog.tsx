import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
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
import { messageFor } from "@/lib/api/error-messages";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { costStrings } from "@/lib/strings/costs";
import { en, fill } from "@/lib/strings/en";
import {
	AMOUNT_RE,
	microToInput,
	type ProviderBalance,
	type ProviderSettingsBody,
	providerPath,
	useUpdateProvider,
} from "./providers";

const t = costStrings.providers.dialog;

const amount = z
	.string()
	.trim()
	.refine((v) => v === "" || AMOUNT_RE.test(v), t.amountInvalid);

const schema = z.object({
	label: z.string().trim().max(100),
	balance: amount,
	balanceAt: z.string(),
	note: z.string().max(500),
	threshold: amount,
	key: z.string().max(500),
});
type Values = z.infer<typeof schema>;

const day = (iso: string | null | undefined): string => (iso ? iso.slice(0, 10) : "");

function valuesOf(p: ProviderBalance | null): Values {
	return {
		label: p?.label ?? "",
		balance: microToInput(p?.manual_balance_usd_micro),
		balanceAt: day(p?.manual_balance_at),
		note: p?.manual_note ?? "",
		threshold: microToInput(p?.low_threshold_usd_micro),
		key: "", // a saved key is never sent back to the browser: the field always starts empty
	};
}

/** What changed, as the engine's PATCH-like body. Only the fields that differ are sent. */
export function bodyFrom(v: Values, p: ProviderBalance | null, flags: { removeKey: boolean; removeBalance: boolean }) {
	const before = valuesOf(p);
	const body: ProviderSettingsBody = { clear_admin_key: false, clear_manual: false };
	const label = v.label.trim();
	if (label && label !== before.label) body.label = label;
	if (flags.removeBalance) {
		body.clear_manual = true;
	} else if (v.balance !== before.balance) {
		if (v.balance !== "") {
			body.manual_balance_usd = v.balance;
			if (v.balanceAt) body.manual_balance_at = `${v.balanceAt}T00:00:00Z`;
		} else if (before.balance !== "") {
			body.clear_manual = true;
		}
	} else if (v.balanceAt && v.balanceAt !== before.balanceAt && v.balance !== "") {
		body.manual_balance_at = `${v.balanceAt}T00:00:00Z`;
	}
	if (v.note !== before.note) body.manual_note = v.note.trim() || null;
	if (v.threshold !== before.threshold) body.low_threshold_usd = v.threshold === "" ? null : v.threshold;
	if (flags.removeKey) body.clear_admin_key = true;
	else if (v.key.trim()) body.admin_key = v.key.trim();
	return body;
}

/**
 * One provider's settings: a manual balance with the date it was true, a note, an alert level and
 * (OpenRouter, OpenAI, Anthropic) the admin key. The key is a password field that starts empty
 * and says "saved" when one is stored; its value never comes back from the engine.
 * `provider === null` adds a new manual balance.
 */
export function ProviderDialog({
	open,
	onOpenChange,
	provider,
}: {
	open: boolean;
	onOpenChange: (o: boolean) => void;
	provider: ProviderBalance | null;
}) {
	const ids = {
		label: useId(),
		balance: useId(),
		at: useId(),
		note: useId(),
		threshold: useId(),
		key: useId(),
	};
	const update = useUpdateProvider();
	const form = useAppForm<Values>({ resolver: zodResolver(schema), defaultValues: valuesOf(provider) });
	const errs = form.formState.errors;
	const [removeKey, setRemoveKey] = useState(false);
	const [removeBalance, setRemoveBalance] = useState(false);
	const { reset } = form;
	useEffect(() => {
		if (open) {
			reset(valuesOf(provider));
			setRemoveKey(false);
			setRemoveBalance(false);
		}
	}, [open, provider, reset]);

	const isNew = provider === null;
	const isManual = isNew || provider.provider === "manual";
	const name = provider?.label ?? t.newTitle;
	// A key set in the engine's environment wins over a saved one and cannot be removed here.
	const keyFromEnv = provider?.admin_key_source === "env";

	const submit = useAppSubmit(
		form,
		async (v) => {
			if (isNew && !v.label.trim()) {
				form.setError("label", { message: t.labelRequired });
				return;
			}
			const body = bodyFrom(v, provider, { removeKey, removeBalance });
			const saved = await update.mutateAsync({ path: provider ? providerPath(provider) : "manual:new", body });
			toast.success(fill(t.saved, { provider: saved.label }));
			onOpenChange(false);
		},
		{
			fieldName: (path) =>
				path === "manual_balance_usd"
					? "balance"
					: path === "low_threshold_usd"
						? "threshold"
						: path === "admin_key"
							? "key"
							: null,
		},
	);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[520px]">
				<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
					<DialogHeader>
						<DialogTitle>{isNew ? t.newTitle : fill(t.title, { provider: name })}</DialogTitle>
						<DialogDescription>{t.lead}</DialogDescription>
					</DialogHeader>
					{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
					{isManual ? (
						<Field id={ids.label} label={t.label} hint={t.labelHint} error={errs.label?.message}>
							<Input
								id={ids.label}
								autoComplete="off"
								maxLength={100}
								aria-invalid={!!errs.label || undefined}
								aria-describedby={describedBy(ids.label, { hint: true, error: !!errs.label })}
								{...form.register("label")}
							/>
						</Field>
					) : null}
					<div className="grid gap-4 min-[520px]:grid-cols-2">
						<Field id={ids.balance} label={t.balance} hint={t.balanceHint} error={errs.balance?.message}>
							<Input
								id={ids.balance}
								inputMode="decimal"
								autoComplete="off"
								disabled={removeBalance}
								aria-invalid={!!errs.balance || undefined}
								aria-describedby={describedBy(ids.balance, { hint: true, error: !!errs.balance })}
								{...form.register("balance")}
							/>
						</Field>
						<Field id={ids.at} label={t.balanceAt}>
							<Input id={ids.at} type="date" disabled={removeBalance} {...form.register("balanceAt")} />
						</Field>
					</div>
					{provider?.manual_balance_usd_micro != null ? (
						<div>
							<Button
								type="button"
								size="sm"
								variant="ghost"
								aria-pressed={removeBalance}
								onClick={() => setRemoveBalance((v) => !v)}
							>
								{removeBalance ? en.common.cancel : t.removeBalance}
							</Button>
						</div>
					) : null}
					<Field id={ids.threshold} label={t.threshold} hint={t.thresholdHint} error={errs.threshold?.message}>
						<Input
							id={ids.threshold}
							inputMode="decimal"
							autoComplete="off"
							aria-invalid={!!errs.threshold || undefined}
							aria-describedby={describedBy(ids.threshold, { hint: true, error: !!errs.threshold })}
							{...form.register("threshold")}
						/>
					</Field>
					<Field id={ids.note} label={t.note} hint={t.noteHint} error={errs.note?.message}>
						<Input
							id={ids.note}
							autoComplete="off"
							maxLength={500}
							aria-describedby={describedBy(ids.note, { hint: true, error: !!errs.note })}
							{...form.register("note")}
						/>
					</Field>
					{!isManual && !keyFromEnv ? (
						<Field
							id={ids.key}
							label={t.key}
							hint={keyFromEnv ? t.keyFromEnvHint : provider?.admin_key_set ? t.keySavedHint : t.keyHint}
							error={errs.key?.message}
						>
							<div className="flex flex-wrap items-center gap-2">
								<Input
									id={ids.key}
									type="password"
									autoComplete="new-password"
									spellCheck={false}
									className="min-w-[200px] flex-1 font-mono text-[12.5px]"
									maxLength={500}
									disabled={removeKey}
									placeholder={provider?.admin_key_set ? "••••••••" : ""}
									aria-describedby={describedBy(ids.key, { hint: true, error: !!errs.key })}
									{...form.register("key")}
								/>
								{provider?.admin_key_set ? (
									<Button
										type="button"
										size="sm"
										variant="ghost"
										aria-pressed={removeKey}
										onClick={() => setRemoveKey((v) => !v)}
									>
										{removeKey ? en.common.cancel : t.removeKey}
									</Button>
								) : null}
							</div>
						</Field>
					) : null}
					{!isManual && keyFromEnv ? (
						<p className="m-0 text-[12.5px] text-muted-foreground">{t.keyFromEnvHint}</p>
					) : null}
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton
							variant="default"
							pending={submit.isSubmitting}
							locked={submit.locked}
							pendingLabel={t.saving}
						>
							{t.save}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
