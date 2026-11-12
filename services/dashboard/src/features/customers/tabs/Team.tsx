import { useQuery } from "@tanstack/react-query";
import { UserPlus } from "lucide-react";
import { useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { teamQuery } from "@/features/team/api";
import { InvitationsCard } from "@/features/team/InvitationsList";
import { InviteDialog } from "@/features/team/InviteDialog";
import { MembersCard } from "@/features/team/MembersTable";
import type { AdminTenant } from "@/lib/api/accounts-types";
import { teamStrings } from "@/lib/strings/accounts";

/**
 * The customer's team as the operator sees it (A5 admin routes): change roles, remove, revoke
 * invitations, and invite with an `accept_url` to copy.
 */
export function TeamTab({ tenant }: { tenant: AdminTenant }) {
	const scope = { kind: "admin", tenantId: tenant.id } as const;
	const team = useQuery(teamQuery(scope));
	const [inviting, setInviting] = useState(false);
	return (
		<div className="grid gap-4">
			<div className="flex justify-end">
				<Button variant="default" onClick={() => setInviting(true)}>
					<UserPlus aria-hidden />
					{teamStrings.page.invite}
				</Button>
			</div>
			{team.isPending ? (
				<CardSkeleton lines={4} />
			) : team.isError ? (
				<ErrorState error={team.error} title={teamStrings.members.error} onRetry={() => void team.refetch()} />
			) : (
				<>
					<MembersCard scope={scope} members={team.data.members} tenantName={tenant.name} selfId={null} />
					<InvitationsCard scope={scope} invitations={team.data.invitations} />
				</>
			)}
			<InviteDialog open={inviting} onOpenChange={setInviting} scope={scope} tenantName={tenant.name} />
		</div>
	);
}
