import { Mail } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { AcceptLink } from "@/features/customers/InviteOwnerStep";
import { Panel } from "@/features/kbs/ui";
import type { AdminInvitation, Invitation } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatShortDate } from "@/lib/format/date";
import { customersStrings, teamStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { type TeamScope, useResendInvitation, useRevokeInvitation } from "./api";
import { roleLabel } from "./MembersTable";

const t2 = t.invitations;

/**
 * Open invitations (mockup `#invList`): e-mail, role, inviter and expiry; Resend and Revoke (plain
 * confirm), both owner-gated. The operator's Resend (A8) shows the new link to copy.
 */
export function InvitationsCard({ scope, invitations }: { scope: TeamScope; invitations: Invitation[] }) {
	const resend = useResendInvitation(scope);
	const [resent, setResent] = useState<AdminInvitation | null>(null);
	const revoke = useRevokeInvitation(scope);
	const { supportEmail } = useBranding();
	const [revoking, setRevoking] = useState<Invitation | null>(null);
	const [error, setError] = useState<string | null>(null);

	const toastError = (e: unknown) => {
		const err = toApiError(e);
		const msg =
			err.code === "email_not_configured"
				? { title: fill(t.invite.noEmail, { support: supportEmail ?? t.invite.noEmailFallback }) }
				: messageFor(err);
		toast.error(msg.title, "body" in msg && msg.body ? { description: msg.body } : undefined);
	};

	const doResend = async (inv: Invitation) => {
		try {
			const r = await resend.mutateAsync(inv.id);
			toast.success(t2.resentToast, { description: fill(t2.resentToastBody, { email: inv.email }) });
			if (r.accept_url) setResent({ ...r, accept_url: r.accept_url });
		} catch (e) {
			toastError(e);
		}
	};

	const doRevoke = async () => {
		if (!revoking) return;
		try {
			await revoke.mutateAsync(revoking.id);
			toast.success(t2.revokedToast, { description: fill(t2.revokedToastBody, { email: revoking.email }) });
			setRevoking(null);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};

	return (
		<Panel title={t2.title} sub={t2.sub}>
			{invitations.length === 0 ? (
				<p className="m-0 px-4 py-3 text-[13px] text-muted-foreground">{t2.empty}</p>
			) : (
				<ul aria-label={t2.label} className="m-0 grid list-none p-0">
					{invitations.map((inv) => (
						<li
							key={inv.id}
							data-testid="invitation"
							className="flex min-w-0 flex-wrap items-center gap-3 border-b border-border px-4 py-2.5 last:border-b-0"
						>
							<Mail className="size-4 shrink-0 text-muted-foreground" aria-hidden />
							<div className="min-w-0 flex-1">
								<div className="truncate font-semibold">{inv.email}</div>
								<div className="text-[12.5px] text-muted-foreground">
									{fill(t2.line, {
										role: roleLabel(inv.role),
										by: inv.invited_by,
										date: formatShortDate(inv.expires_at),
									})}
								</div>
							</div>
							<div className="flex gap-1.5">
								<OwnerGate action="team.manage">
									<Button
										size="sm"
										aria-label={fill(t2.forEmail, { action: t2.resend, email: inv.email })}
										disabled={resend.isPending}
										onClick={() => void doResend(inv)}
									>
										{t2.resend}
									</Button>
								</OwnerGate>
								<OwnerGate action="team.manage">
									<Button
										size="sm"
										variant="ghost"
										aria-label={fill(t2.forEmail, { action: t2.revoke, email: inv.email })}
										onClick={() => {
											setError(null);
											setRevoking(inv);
										}}
									>
										{t2.revoke}
									</Button>
								</OwnerGate>
							</div>
						</li>
					))}
				</ul>
			)}
			<Dialog open={resent !== null} onOpenChange={(o) => (o ? undefined : setResent(null))}>
				<DialogContent className="sm:max-w-[520px]">
					<DialogHeader>
						<DialogTitle>{fill(customersStrings.invite.sentTitle, { email: resent?.email ?? "" })}</DialogTitle>
						<DialogDescription className="sr-only">{t2.resentToast}</DialogDescription>
					</DialogHeader>
					{resent ? <AcceptLink invitation={resent} /> : null}
					<DialogFooter>
						<Button type="button" variant="default" onClick={() => setResent(null)}>
							{customersStrings.invite.done}
						</Button>
					</DialogFooter>
				</DialogContent>
			</Dialog>
			<ConfirmDialog
				open={revoking !== null}
				onOpenChange={(o) => (o ? undefined : setRevoking(null))}
				title={fill(t2.revokeTitle, { email: revoking?.email ?? "" })}
				body={t2.revokeBody}
				confirmLabel={t2.revokeConfirm}
				pending={revoke.isPending}
				onConfirm={() => void doRevoke()}
			>
				{error ? <FormAlert>{error}</FormAlert> : null}
			</ConfirmDialog>
		</Panel>
	);
}
