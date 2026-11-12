import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useId } from "react";
import { toast } from "sonner";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
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
import { AdminInviteForm, type InviteValues, inviteSchema } from "@/features/customers/InviteOwnerStep";
import { messageFor } from "@/lib/api/error-messages";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { customersStrings, teamStrings as t } from "@/lib/strings/accounts";
import { en, fill } from "@/lib/strings/en";
import { type TeamScope, useInvite } from "./api";
import { roleLabel } from "./MembersTable";

/**
 * Invite a teammate (mockup `#inviteDlg`). `me`: `POST /tenants/me/members {email, role}` (owners;
 * the link is only e-mailed). `admin`: the operator's invitation, which shows `accept_url`.
 */
export function InviteDialog({
	open,
	onOpenChange,
	scope,
	tenantName,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	scope: TeamScope;
	tenantName: string;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[520px]">
				{!open ? null : scope.kind === "admin" ? (
					<AdminInviteForm
						tenant={{ id: scope.tenantId, name: tenantName }}
						title={fill(t.invite.title, { tenant: tenantName })}
						lead={customersStrings.invite.lead}
						secondary={
							<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
								{en.common.cancel}
							</Button>
						}
						after={(reset) => (
							<>
								<Button type="button" variant="ghost" onClick={reset}>
									{customersStrings.invite.another}
								</Button>
								<Button type="button" variant="default" onClick={() => onOpenChange(false)}>
									{customersStrings.invite.done}
								</Button>
							</>
						)}
					/>
				) : (
					<MeInviteForm tenantName={tenantName} onDone={() => onOpenChange(false)} />
				)}
			</DialogContent>
		</Dialog>
	);
}

function MeInviteForm({ tenantName, onDone }: { tenantName: string; onDone: () => void }) {
	const ids = { email: useId(), role: useId() };
	const invite = useInvite({ kind: "me" });
	const { supportEmail } = useBranding();
	const form = useAppForm<InviteValues>({
		resolver: zodResolver(inviteSchema),
		defaultValues: { email: "", role: "member" },
	});
	const errs = form.formState.errors;
	const { setFocus } = form;
	useEffect(() => {
		setFocus("email");
	}, [setFocus]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const inv = await invite.mutateAsync({ email: v.email.trim(), role: v.role });
			toast.success(t.invite.sentToast, {
				description: fill(t.invite.sentToastBody, { email: inv.email, role: roleLabel(inv.role) }),
			});
			onDone();
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

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{fill(t.invite.title, { tenant: tenantName })}</DialogTitle>
				<DialogDescription>{t.invite.lead}</DialogDescription>
			</DialogHeader>
			{submit.error?.code === "email_not_configured" ? (
				<FormAlert>{fill(t.invite.noEmail, { support: supportEmail ?? t.invite.noEmailFallback })}</FormAlert>
			) : submit.error ? (
				<ErrorAlert error={submit.error} until={submit.until} />
			) : null}
			<Field id={ids.email} label={t.invite.email} error={errs.email?.message}>
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
			<Field id={ids.role} label={t.invite.role}>
				<NativeSelect id={ids.role} {...form.register("role")}>
					<option value="member">{t.invite.roleMember}</option>
					<option value="owner">{t.invite.roleOwner}</option>
				</NativeSelect>
			</Field>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					{en.common.cancel}
				</Button>
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
