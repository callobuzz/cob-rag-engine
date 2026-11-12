import { createFileRoute, redirect, useNavigate } from "@tanstack/react-router";
import { CirclePause, Clock } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { AuthLayout } from "@/components/auth/AuthLayout";
import { ContactBox } from "@/components/auth/ContactBox";
import { StatusCard } from "@/components/auth/StatusCard";
import { WorkspaceList } from "@/components/shell/WorkspaceSwitcher";
import { Button } from "@/components/ui/button";
import { signOut } from "@/lib/auth/actions";
import { firstName, isInactive } from "@/lib/auth/principal";
import { refreshSession } from "@/lib/auth/refresh";
import { getAuthState, setAuthState, useAuth } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { en, fill } from "@/lib/strings/en";

/**
 * Pending / suspended workspace page (mockup `[data-auth=pending]`, `[data-auth=suspended]`).
 * It may only call what A4 allows an inactive tenant's session: /auth/*, GET /whoami,
 * GET /tenants/me and public branding — never /info, corpora or other shell data.
 */
export const Route = createFileRoute("/status")({
	beforeLoad: async ({ context }) => {
		if (context.auth.hasCredential()) {
			const r = await context.auth.ensurePrincipal();
			if (r.ok && !isInactive(r.principal) && !getAuthState().lastWorkspaceStatus) throw redirect({ to: "/" });
			if (r.ok || getAuthState().lastWorkspaceStatus) return;
		}
		if (!getAuthState().lastWorkspaceStatus) throw redirect({ to: "/login" });
	},
	component: StatusPage,
});

function StatusPage() {
	const { principal, lastWorkspaceStatus, credential } = useAuth();
	const { productName } = useBranding();
	const navigate = useNavigate();
	const [checking, setChecking] = useState(false);
	const status =
		principal?.tenant?.status !== "active" && principal?.tenant ? principal.tenant.status : lastWorkspaceStatus;
	const tenantName = principal?.tenant?.name;

	const logOut = async () => {
		if (credential) await signOut(null);
		else {
			setAuthState({ lastWorkspaceStatus: null });
			await navigate({ to: "/login" });
		}
	};

	const checkAgain = async () => {
		setChecking(true);
		try {
			const outcome = await refreshSession();
			const p = getAuthState().principal;
			if (outcome === "ok" && p && !isInactive(p)) {
				setAuthState({ lastWorkspaceStatus: null });
				await navigate({ to: "/" });
				return;
			}
			if (outcome !== "signed-out") toast(en.status.stillWaiting, { description: en.status.stillWaitingBody });
		} finally {
			setChecking(false);
		}
	};

	// A tk_ key of an inactive tenant: tenant name unknown, nothing stored.
	if (!principal || !tenantName) {
		return (
			<AuthLayout title={en.status.keyTitle}>
				<StatusCard icon={CirclePause} tone="stop" title={en.status.keyTitle}>
					<p className="m-0 text-foreground-2">{en.status.keyBody}</p>
					<ContactBox heading={en.status.contactSuspended} />
					<Button variant="ghost" onClick={() => void logOut()}>
						{en.common.backToLogin}
					</Button>
				</StatusCard>
			</AuthLayout>
		);
	}

	if (status === "suspended") {
		const title = fill(en.status.suspendedTitle, { tenant: tenantName });
		return (
			<AuthLayout title={title}>
				<StatusCard icon={CirclePause} tone="stop" title={title}>
					<p className="m-0 text-foreground-2">
						{en.status.suspendedBody} <b className="text-foreground">{en.status.safe}</b>
					</p>
					<ContactBox heading={en.status.contactSuspended} />
					<WorkspaceList />
					<Button variant="ghost" onClick={() => void logOut()}>
						{en.common.logOut}
					</Button>
				</StatusCard>
			</AuthLayout>
		);
	}

	const [before, after] = en.status.pendingBody.split("{tenant}");
	return (
		<AuthLayout title={en.status.pendingTitle}>
			<StatusCard icon={Clock} tone="wait" title={en.status.pendingTitle}>
				<p className="m-0 text-foreground-2">
					{fill(before ?? "", { first: firstName(principal), product: productName })}
					<b className="text-foreground">{tenantName}</b>
					{fill(after ?? "", { first: firstName(principal), product: productName })}
				</p>
				<ContactBox heading={en.status.contactPending} />
				<WorkspaceList />
				<div className="flex gap-2">
					{principal.auth === "session" ? (
						<Button disabled={checking} onClick={() => void checkAgain()}>
							{en.status.checkAgain}
						</Button>
					) : null}
					<Button variant="ghost" onClick={() => void logOut()}>
						{en.common.logOut}
					</Button>
				</div>
			</StatusCard>
		</AuthLayout>
	);
}
