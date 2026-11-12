import { zodResolver } from "@hookform/resolvers/zod";
import { RefreshCw } from "lucide-react";
import { useEffect, useId } from "react";
import { Controller, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { CopyButton } from "@/components/patterns/CopyButton";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
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
import { webhooksStrings } from "@/lib/strings/admin";
import { en, fill } from "@/lib/strings/en";
import { generateSecret, useCreateWebhook, WEBHOOK_EVENTS, type WebhookEvent } from "./api";

const t = webhooksStrings.dialog;

function validUrl(v: string): boolean {
	try {
		const u = new URL(v);
		return (u.protocol === "https:" || u.protocol === "http:") && !!u.hostname;
	} catch {
		return false;
	}
}

const webhookSchema = z.object({
	url: z.string().refine((v) => validUrl(v.trim()), t.urlInvalid),
	events: z.array(z.enum(WEBHOOK_EVENTS as [WebhookEvent, ...WebhookEvent[]])).min(1, t.eventsNone),
	secret: z.string().min(16, t.secretInvalid).max(256, t.secretInvalid),
});
type WebhookValues = z.infer<typeof webhookSchema>;

const emptyValues = (): WebhookValues => ({ url: "", events: ["knowledge_gap.detected"], secret: generateSecret() });

/**
 * New webhook (URL, events with plain labels, secret). The secret is made in the browser
 * (`crypto.getRandomValues`, 32 bytes base64url), editable, with Copy — the engine never shows it.
 */
export function WebhookDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (o: boolean) => void }) {
	const ids = { url: useId(), events: useId(), secret: useId() };
	const create = useCreateWebhook();
	const form = useAppForm<WebhookValues>({ resolver: zodResolver(webhookSchema), defaultValues: emptyValues() });
	const errs = form.formState.errors;
	const secret = useWatch({ control: form.control, name: "secret" });
	const { reset } = form;

	useEffect(() => {
		if (open) reset(emptyValues());
	}, [open, reset]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const w = await create.mutateAsync({ url: v.url.trim(), events: v.events, secret: v.secret });
			toast.success(t.created, { description: fill(t.createdBody, { url: w.url }) });
			onOpenChange(false);
		},
		{
			fieldName: (path) =>
				path === "url" ? "url" : path.startsWith("events") ? "events" : path === "secret" ? "secret" : null,
		},
	);

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[560px]">
				<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
					<DialogHeader>
						<DialogTitle>{t.title}</DialogTitle>
						<DialogDescription>{t.lead}</DialogDescription>
					</DialogHeader>
					{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
					<Field id={ids.url} label={t.url} error={errs.url?.message}>
						<Input
							id={ids.url}
							type="url"
							inputMode="url"
							autoComplete="off"
							className="font-mono"
							placeholder={t.urlPlaceholder}
							aria-invalid={!!errs.url || undefined}
							aria-describedby={describedBy(ids.url, { error: !!errs.url })}
							{...form.register("url")}
						/>
					</Field>
					<Controller
						control={form.control}
						name="events"
						render={({ field }) => (
							<fieldset
								className="grid gap-2"
								aria-describedby={errs.events ? `${ids.events}-error` : undefined}
								aria-invalid={errs.events ? true : undefined}
							>
								<legend className="mb-1 text-[13px] font-semibold">{t.events}</legend>
								{WEBHOOK_EVENTS.map((ev) => {
									const id = `${ids.events}-${ev}`;
									return (
										<div key={ev} className="flex items-center gap-2.5">
											<Checkbox
												id={id}
												ref={ev === WEBHOOK_EVENTS[0] ? field.ref : undefined}
												checked={field.value.includes(ev)}
												onBlur={field.onBlur}
												onCheckedChange={(c) =>
													field.onChange(
														c === true
															? WEBHOOK_EVENTS.filter((x) => x === ev || field.value.includes(x))
															: field.value.filter((x) => x !== ev),
													)
												}
											/>
											<label htmlFor={id} className="text-[13.5px]">
												{webhooksStrings.events[ev]}{" "}
												<code className="font-mono text-[11.5px] text-muted-foreground">{ev}</code>
											</label>
										</div>
									);
								})}
								{errs.events ? (
									<small id={`${ids.events}-error`} className="text-[12.5px] font-medium text-destructive">
										{errs.events.message}
									</small>
								) : null}
							</fieldset>
						)}
					/>
					<Field id={ids.secret} label={t.secret} hint={t.secretHint} error={errs.secret?.message}>
						<div className="flex flex-wrap items-center gap-2">
							<Input
								id={ids.secret}
								autoComplete="off"
								spellCheck={false}
								className="min-w-[200px] flex-1 font-mono text-[12.5px]"
								maxLength={256}
								aria-invalid={!!errs.secret || undefined}
								aria-describedby={describedBy(ids.secret, { hint: true, error: !!errs.secret })}
								{...form.register("secret")}
							/>
							<CopyButton value={secret} ariaLabel={`${en.common.copy} ${t.secret.toLowerCase()}`} />
							<Button
								type="button"
								size="sm"
								variant="ghost"
								onClick={() => form.setValue("secret", generateSecret(), { shouldValidate: true, shouldDirty: true })}
							>
								<RefreshCw aria-hidden />
								{t.regenerate}
							</Button>
						</div>
					</Field>
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton
							variant="default"
							pending={submit.isSubmitting}
							locked={submit.locked}
							pendingLabel={t.creating}
						>
							{t.create}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
