import { Lock } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import type { Member, MemberRole } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { signOut } from "@/lib/auth/actions";
import { initials } from "@/lib/auth/principal";
import { formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { teamStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { type TeamScope, useChangeRole, useLeaveWorkspace, useRemoveMember } from "./api";

const t2 = t.members;

export function roleLabel(role: MemberRole): string {
	return role === "owner" ? t2.owner : t2.member;
}

/** How many owners; the last one can't leave or be demoted (the server's `last_owner` decides). */
export function ownerCount(members: Member[]): number {
	return members.filter((m) => m.role === "owner").length;
}

export function isLastOwner(m: Member, members: Member[]): boolean {
	return m.role === "owner" && ownerCount(members) === 1;
}

function Person({ m, self }: { m: Member; self: boolean }) {
	return (
		<div className="flex min-w-0 items-center gap-2.5">
			<span
				aria-hidden
				className="grid size-8 shrink-0 place-items-center rounded-full bg-[linear-gradient(135deg,#2f8cff,#3fd8f5)] text-[11px] font-bold text-[#04101f]"
			>
				{initials(m.name || m.email)}
			</span>
			<div className="min-w-0">
				<b className="block truncate font-semibold">
					{m.name || m.email}
					{self ? (
						<span className="ml-1.5 rounded-full bg-primary-soft px-1.5 py-px align-[1px] font-mono text-[10.5px] font-medium text-primary">
							{t2.you}
						</span>
					) : null}
				</b>
				<small className="block truncate text-[12px] text-muted-foreground">{m.email}</small>
			</div>
		</div>
	);
}

/**
 * Members card (mockup `[data-view=team]`): person, role, last active, and actions. Owners (and
 * the operator) change roles and remove people; the last owner shows "Last owner" instead;
 * anyone may leave from their own row (`me` scope only).
 */
export function MembersCard({
	scope,
	members,
	tenantName,
	selfId,
}: {
	scope: TeamScope;
	members: Member[];
	tenantName: string;
	/** The signed-in person's user id (`me` scope); null for the operator or a tk_ key. */
	selfId: string | null;
}) {
	const changeRole = useChangeRole(scope);
	const remove = useRemoveMember(scope);
	const leave = useLeaveWorkspace();
	const [removing, setRemoving] = useState<Member | null>(null);
	const [leaving, setLeaving] = useState<Member | null>(null);
	const [dialogError, setDialogError] = useState<string | null>(null);
	const onlyOwner = ownerCount(members) === 1 ? members.find((m) => m.role === "owner") : undefined;

	const showError = (e: unknown) => {
		const msg = messageFor(toApiError(e));
		toast.error(msg.title, msg.body ? { description: msg.body } : undefined);
	};

	const setRole = async (m: Member, role: MemberRole) => {
		try {
			await changeRole.mutateAsync({ userId: m.user_id, role });
			toast.success(
				fill(t2.roleToast, { name: m.name || m.email, role: role === "owner" ? t2.roleOwner : t2.roleMember }),
			);
		} catch (e) {
			showError(e);
		}
	};

	const doRemove = async () => {
		if (!removing) return;
		try {
			await remove.mutateAsync(removing.user_id);
			toast.success(fill(t2.removedToast, { name: removing.name || removing.email }), {
				description: fill(t2.removedToastBody, { tenant: tenantName }),
			});
			setRemoving(null);
		} catch (e) {
			setDialogError(messageFor(toApiError(e)).title);
		}
	};

	const doLeave = async () => {
		if (!leaving) return;
		try {
			await leave.mutateAsync(leaving.user_id);
			setLeaving(null);
			toast.success(fill(t2.leftToast, { tenant: tenantName }));
			// The engine ended this session with the membership: sign out here without a server call.
			await signOut(null, { callServer: false });
		} catch (e) {
			setDialogError(messageFor(toApiError(e)).title);
		}
	};

	const columns: Column<Member>[] = [
		{
			id: "person",
			header: t2.cols.person,
			rowHeader: true,
			sortValue: (m) => (m.name || m.email).toLowerCase(),
			cell: (m) => <Person m={m} self={m.user_id === selfId} />,
		},
		{
			id: "role",
			header: t2.cols.role,
			stackLabel: true,
			sortValue: (m) => m.role,
			cell: (m) => <Badge variant={m.role === "owner" ? "info" : "default"}>{roleLabel(m.role)}</Badge>,
		},
		{
			id: "lastActive",
			header: t2.cols.lastActive,
			stackLabel: true,
			sortValue: (m) => m.last_login_at ?? "",
			cell: (m) => (
				<span title={m.last_login_at ?? undefined}>{m.last_login_at ? formatRelative(m.last_login_at) : t2.never}</span>
			),
		},
		{
			id: "actions",
			header: t2.cols.actions,
			align: "right",
			cell: (m) => {
				const name = m.name || m.email;
				const self = m.user_id === selfId;
				const last = isLastOwner(m, members);
				const toRole: MemberRole = m.role === "owner" ? "member" : "owner";
				const roleText = toRole === "owner" ? t2.makeOwner : t2.makeMember;
				return (
					<div className="flex flex-wrap items-center justify-end gap-1.5 max-[600px]:justify-start">
						{last ? (
							<span className="inline-flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
								<Lock className="size-3.5" aria-hidden />
								{t2.lastOwner}
							</span>
						) : (
							<OwnerGate action="team.manage">
								<Button
									size="sm"
									aria-label={fill(t2.forName, { action: roleText, name })}
									disabled={changeRole.isPending}
									onClick={() => void setRole(m, toRole)}
								>
									{roleText}
								</Button>
							</OwnerGate>
						)}
						{self ? (
							last ? null : (
								<Button
									size="sm"
									variant="ghost"
									onClick={() => {
										setDialogError(null);
										setLeaving(m);
									}}
								>
									{t2.leave}
								</Button>
							)
						) : last ? null : (
							<OwnerGate action="team.manage">
								<Button
									size="sm"
									variant="destructive"
									aria-label={fill(t2.forName, { action: t2.remove, name })}
									onClick={() => {
										setDialogError(null);
										setRemoving(m);
									}}
								>
									{t2.remove}
								</Button>
							</OwnerGate>
						)}
					</div>
				);
			},
		},
	];

	return (
		<Panel
			title={t2.title}
			sub={members.length === 1 ? t2.subOne : fill(t2.sub, { count: formatNumber(members.length) })}
		>
			<div className="overflow-x-auto">
				<DataTable
					caption={fill(t2.caption, { tenant: tenantName })}
					columns={columns}
					rows={members}
					rowKey={(m) => m.user_id}
				/>
			</div>
			{onlyOwner ? (
				<div className="flex items-start gap-2.5 rounded-b-lg border-t border-border bg-secondary px-4 py-3 text-[13px] text-foreground-2">
					<Lock className="mt-px size-4 shrink-0 text-muted-foreground" aria-hidden />
					<span>{fill(t2.lastOwnerNote, { name: onlyOwner.name || onlyOwner.email })}</span>
				</div>
			) : null}
			<ConfirmDialog
				open={removing !== null}
				onOpenChange={(o) => (o ? undefined : setRemoving(null))}
				title={fill(t2.removeTitle, { name: removing?.name || removing?.email || "", tenant: tenantName })}
				body={fill(t2.removeBody, { name: removing?.name || removing?.email || "", tenant: tenantName })}
				confirmLabel={t2.removeConfirm}
				pending={remove.isPending}
				onConfirm={() => void doRemove()}
			>
				{dialogError ? <FormAlert>{dialogError}</FormAlert> : null}
			</ConfirmDialog>
			<ConfirmDialog
				open={leaving !== null}
				onOpenChange={(o) => (o ? undefined : setLeaving(null))}
				title={fill(t2.leaveTitle, { tenant: tenantName })}
				body={fill(t2.leaveBody, { tenant: tenantName })}
				confirmLabel={t2.leaveConfirm}
				pending={leave.isPending}
				onConfirm={() => void doLeave()}
			>
				{dialogError ? <FormAlert>{dialogError}</FormAlert> : null}
			</ConfirmDialog>
		</Panel>
	);
}
