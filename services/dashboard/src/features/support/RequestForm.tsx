import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { recentClientErrors } from "@/lib/api/recent-errors";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { supportStrings } from "@/lib/strings/support";
import { useCreateSupportRequest, validateRequest } from "./api";

const s = supportStrings;

/**
 * The form that writes to the provider (TV3): a subject and what happened. The page the person
 * was on and the last few error codes the dashboard saw travel with it; the engine adds the plan
 * and the workspace's state.
 */
export function RequestForm({
	page,
	onSent,
	onCancel,
}: {
	page?: string;
	onSent: (id: string) => void;
	onCancel: () => void;
}) {
	const id = useId();
	const { productName } = useBranding();
	const create = useCreateSupportRequest();
	const [subject, setSubject] = useState("");
	const [message, setMessage] = useState("");
	const [errors, setErrors] = useState<Partial<Record<"subject" | "message", string>>>({});
	const [failure, setFailure] = useState<string | null>(null);

	const submit = async (e: React.FormEvent) => {
		e.preventDefault();
		setFailure(null);
		const found = validateRequest(subject, message, s.form.errors);
		setErrors(found);
		if (found.subject || found.message) return;
		try {
			const sent = await create.mutateAsync({
				subject: subject.trim(),
				message,
				...(page ? { page } : {}),
				client_errors: recentClientErrors(),
			});
			toast.success(s.form.sent, { description: fill(s.form.sentBody, { product: productName }) });
			onSent(sent.id);
		} catch (err) {
			setFailure(messageFor(toApiError(err)).title);
		}
	};

	return (
		<form onSubmit={(e) => void submit(e)} noValidate data-testid="support-form" className="grid gap-4 p-4">
			<h2 className="text-[15px] font-bold">{fill(s.form.title, { product: productName })}</h2>
			{failure ? <FormAlert>{failure}</FormAlert> : null}
			<Field id={`${id}-subject`} label={s.form.subject} hint={s.form.subjectHint} error={errors.subject}>
				<Input
					id={`${id}-subject`}
					value={subject}
					maxLength={200}
					aria-invalid={errors.subject ? true : undefined}
					aria-describedby={`${id}-subject-hint${errors.subject ? ` ${id}-subject-error` : ""}`}
					onChange={(e) => setSubject(e.target.value)}
				/>
			</Field>
			<Field id={`${id}-message`} label={s.form.message} hint={s.form.messageHint} error={errors.message}>
				<Textarea
					id={`${id}-message`}
					rows={6}
					value={message}
					aria-invalid={errors.message ? true : undefined}
					aria-describedby={`${id}-message-hint${errors.message ? ` ${id}-message-error` : ""}`}
					onChange={(e) => setMessage(e.target.value)}
				/>
			</Field>
			{page ? (
				<p className="m-0 text-[12.5px] text-muted-foreground" data-testid="support-page">
					{s.form.page}: <code>{page}</code>
				</p>
			) : null}
			<div className="flex flex-wrap gap-2">
				<SubmitButton variant="default" pending={create.isPending} pendingLabel={s.form.sending}>
					{s.form.submit}
				</SubmitButton>
				<Button type="button" variant="ghost" onClick={onCancel}>
					{s.form.cancel}
				</Button>
			</div>
		</form>
	);
}
