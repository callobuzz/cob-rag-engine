import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { Link2, MailCheck } from "lucide-react";
import { type ReactNode, useEffect, useId, useState } from "react";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { CopyButton } from "@/components/patterns/CopyButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useInvite } from "@/features/team/api";
import type { AdminInvitation, MemberRole } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { formatDate } from "@/lib/format/date";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { customersStrings as t, teamStrings } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { smtpQuery } from "./api";

export const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

export const inviteSchema = z.object({
	email: z.string().trim().regex(EMAIL_RE, t.invite.emailInvalid),
	role: z.enum(["owner", "member"]),
});
export type InviteValues = z.infer<typeof inviteSchema>;

/**
 * The operator's invitation (A5 admin variant): `POST /admin/tenants/{id}/invitations` answers
 * with `accept_url`, shown with a copy button so onboarding works without SMTP and in Closed
 * mode. When SMTP is configured (A2 `GET /admin/smtp`) the engine e-mails it too.
 */
export function AdminInviteForm({
	tenant,
	fixedRole,
	title,
	lead,
	secondary,
	after,
}: {
	tenant: { id: string; name: string };
	/** "owner" for a new customer's first owner; omitted = pick a role. */
	fixedRole?: MemberRole;
	title: string;
	lead: string;
	/** Footer button next to "Create invitation" (Skip / Cancel). */
	secondary: ReactNode;
	/** Footer once the invitation exists (Done, Open customer…). */
	after: (reset: () => void) => ReactNode;
}) {
	const ids = { email: useId(), role: useId() };
	const invite = useInvite({ kind: "admin", tenantId: tenant.id });
	const form = useAppForm<InviteValues>({
		resolver: zodResolver(inviteSchema),
		defaultValues: { email: "", role: fixedRole ?? "member" },
	});
	const errs = form.formState.errors;
	const [created, setCreated] = useState<AdminInvitation | null>(null);
	const { setFocus } = form;
	useEffect(() => {
		if (!created) setFocus("email");
	}, [created, setFocus]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const inv = await invite.mutateAsync({ email: v.email.trim(), role: v.role });
			setCreated(inv as AdminInvitation);
		},
		{
			fieldName: (path) => (path === "email" ? "email" : null),
			onError: (err) => {
				if (err.code === "already_member" || err.code === "superadmin_email") {
					form.setError("email", { type: "server", message: messageFor(err).title });
					form.setFocus("email");
					return true;
				}
				if (err.status === 422 && err.fieldErrors.some((f) => f.path === "email")) {
					form.setError("email", { type: "server", message: t.invite.emailInvalid });
					form.setFocus("email");
					return true;
				}
				return false;
			},
		},
	);

	if (created) {
		return (
			<div className="grid gap-4">
				<DialogHeader>
					<DialogTitle>{fill(t.invite.sentTitle, { email: created.email })}</DialogTitle>
					<DialogDescription className="sr-only">{lead}</DialogDescription>
				</DialogHeader>
				<AcceptLink invitation={created} />
				<DialogFooter>
					{after(() => {
						setCreated(null);
						form.reset({ email: "", role: fixedRole ?? "member" });
					})}
				</DialogFooter>
			</div>
		);
	}

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{title}</DialogTitle>
				<DialogDescription>{lead}</DialogDescription>
			</DialogHeader>
			{submit.error ? <FormAlert>{messageFor(submit.error, { admin: true }).title}</FormAlert> : null}
			<Field
				id={ids.email}
				label={fixedRole === "owner" ? t.invite.email : teamStrings.invite.email}
				error={errs.email?.message}
			>
				<Input
					id={ids.email}
					type="email"
					autoComplete="off"
					placeholder={t.invite.emailPlaceholder}
					aria-invalid={!!errs.email || undefined}
					aria-describedby={describedBy(ids.email, { error: !!errs.email })}
					{...form.register("email")}
				/>
			</Field>
			{fixedRole ? null : (
				<Field id={ids.role} label={t.invite.role}>
					<NativeSelect id={ids.role} {...form.register("role")}>
						<option value="member">{teamStrings.invite.roleMember}</option>
						<option value="owner">{teamStrings.invite.roleOwner}</option>
					</NativeSelect>
				</Field>
			)}
			<DialogFooter>
				{secondary}
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.invite.sending}
				>
					{t.invite.send}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}

/** The invitation link with copy, and whether the engine e-mailed it. */
export function AcceptLink({ invitation }: { invitation: AdminInvitation }) {
	const smtp = useQuery(smtpQuery);
	const id = useId();
	const emailed = smtp.data?.configured === true;
	return (
		<div className="grid gap-3" data-testid="accept-link">
			<FormAlert tone={emailed ? "info" : "warn"}>
				{fill(emailed ? t.invite.emailed : t.invite.notEmailed, { email: invitation.email })}
			</FormAlert>
			<div className="grid gap-1.5">
				<label htmlFor={id} className="flex items-center gap-1.5 text-[13px] font-semibold">
					{emailed ? <MailCheck className="size-4" aria-hidden /> : <Link2 className="size-4" aria-hidden />}
					{t.invite.linkLabel}
				</label>
				<div className="flex flex-wrap items-center gap-2">
					<Input
						id={id}
						readOnly
						className="min-w-[200px] flex-1 font-mono text-[12.5px]"
						value={invitation.accept_url}
						onFocus={(e) => e.currentTarget.select()}
					/>
					<CopyButton
						value={invitation.accept_url}
						label={t.invite.copyLink}
						ariaLabel={fill(t.invite.copyLinkFor, { email: invitation.email })}
					/>
				</div>
				<small className="text-[12.5px] text-muted-foreground">
					{fill(t.invite.expires, { date: formatDate(invitation.expires_at) })}
				</small>
			</div>
		</div>
	);
}
