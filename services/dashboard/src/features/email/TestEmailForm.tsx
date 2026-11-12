import { zodResolver } from "@hookform/resolvers/zod";
import { Send } from "lucide-react";
import { useId } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Input } from "@/components/ui/input";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { emailStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { useSmtpTest } from "./api";

const t = emailStrings.test;
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

const schema = z.object({ to: z.string().trim().regex(EMAIL_RE, t.invalid) });
type Values = z.infer<typeof schema>;

/**
 * Mockup "Send a test email": recipient (the super admin's own e-mail by default) →
 * `POST /admin/smtp/test`. 503 and 502 show the catalog messages; 422 marks the field.
 */
export function TestEmailForm({ defaultTo }: { defaultTo: string }) {
	const id = useId();
	const test = useSmtpTest();
	const form = useAppForm<Values>({ resolver: zodResolver(schema), defaultValues: { to: defaultTo } });
	const err = form.formState.errors.to?.message;

	const submit = useAppSubmit(
		form,
		async (v) => {
			const r = await test.mutateAsync(v.to.trim());
			if (r.provider === "log") toast.success(t.sentLog, { description: fill(t.sentLogBody, { to: r.to }) });
			else toast.success(t.sentSmtp, { description: fill(t.sentSmtpBody, { to: r.to }) });
		},
		{
			onError: (e) => {
				if (e.status === 422 && e.fieldErrors.some((f) => f.path === "to")) {
					form.setError("to", { type: "server", message: t.invalid });
					form.setFocus("to");
					return true;
				}
				return false;
			},
		},
	);
	const alert = submit.error ? messageFor(submit.error, { admin: true }) : null;

	return (
		<Panel title={t.title} sub={t.sub}>
			<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-3 p-4" aria-label={t.title}>
				{alert ? (
					<FormAlert>
						<b className="font-semibold">{alert.title}</b>
						{alert.body ? <span className="block">{alert.body}</span> : null}
					</FormAlert>
				) : null}
				<Field id={id} label={t.to} error={err}>
					<div className="flex flex-wrap gap-2">
						<Input
							id={id}
							type="email"
							autoComplete="email"
							className="min-w-[200px] flex-1"
							aria-invalid={!!err || undefined}
							aria-describedby={describedBy(id, { error: !!err })}
							{...form.register("to")}
						/>
						<SubmitButton
							variant="default"
							pending={submit.isSubmitting}
							locked={submit.locked}
							pendingLabel={t.sending}
						>
							<Send aria-hidden />
							{t.send}
						</SubmitButton>
					</div>
				</Field>
			</form>
		</Panel>
	);
}
