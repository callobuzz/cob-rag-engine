import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { Lock, UserPlus } from "lucide-react";
import { FormAlert } from "@/components/auth/FormAlert";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { usePrincipal } from "@/lib/auth/store";
import { can } from "@/lib/permissions";
import { teamStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { teamQuery } from "./api";
import { InvitationsCard } from "./InvitationsList";
import { InviteDialog } from "./InviteDialog";
import { MembersCard } from "./MembersTable";
import { RolesTable } from "./RolesTable";

const route = getRouteApi("/_app/team");
const ME = { kind: "me" } as const;

/** `/team` (mockup `[data-view=team]`): members, open invitations, the role table. */
export function TeamPage() {
	const principal = usePrincipal();
	const { invite } = route.useSearch();
	const navigate = useNavigate();
	const team = useQuery(teamQuery(ME));
	const tenantName = principal?.tenant?.name ?? "";
	const owner = can(principal, "team.manage");
	const inviting = invite === 1 && owner;
	const setInviting = (open: boolean) =>
		void navigate({ to: "/team", search: (prev) => ({ ...prev, invite: open ? 1 : undefined }), replace: true });
	const firstOwner = team.data?.members.find((m) => m.role === "owner");

	return (
		<>
			<PageHeader
				title={t.page.title}
				description={fill(t.page.lead, { tenant: tenantName })}
				actions={
					<OwnerGate action="team.manage">
						<Button variant="default" onClick={() => setInviting(true)}>
							<UserPlus aria-hidden />
							{t.page.invite}
						</Button>
					</OwnerGate>
				}
			/>
			{!owner ? (
				<FormAlert tone="info">
					<span className="inline-flex items-start gap-1.5">
						<Lock className="mt-0.5 size-3.5 shrink-0" aria-hidden />
						{fill(t.page.memberNote, { owner: firstOwner?.name || t.page.anOwner })}
					</span>
				</FormAlert>
			) : null}
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
				<div className="grid min-w-0 content-start gap-4">
					{team.isPending ? (
						<>
							<CardSkeleton lines={4} />
							<CardSkeleton lines={2} />
						</>
					) : team.isError ? (
						<ErrorState error={team.error} title={t.members.error} onRetry={() => void team.refetch()} />
					) : (
						<>
							<MembersCard
								scope={ME}
								members={team.data.members}
								tenantName={tenantName}
								selfId={principal?.user?.id ?? null}
							/>
							<InvitationsCard scope={ME} invitations={team.data.invitations} />
						</>
					)}
				</div>
				<RolesTable />
			</div>
			<InviteDialog open={inviting} onOpenChange={setInviting} scope={ME} tenantName={tenantName} />
		</>
	);
}
