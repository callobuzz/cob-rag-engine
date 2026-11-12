import { useNavigate } from "@tanstack/react-router";
import { Check } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { DropdownMenuItem, DropdownMenuLabel } from "@/components/ui/dropdown-menu";
import type { TokenTenant } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { reloadPrincipal, switchWorkspace } from "@/lib/auth/actions";
import { initials } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { kbColor } from "@/lib/kb-selection";
import { en, fill } from "@/lib/strings/en";

/**
 * Workspace switcher over A3 `tenants[]` (replaces design §2's saved connections; D1 decision).
 * Choosing one calls POST /auth/switch-tenant, replaces the access token, clears the query cache
 * and reloads the principal.
 */
export function useSwitchWorkspace() {
	const navigate = useNavigate();
	return async (t: TokenTenant) => {
		try {
			const p = await switchWorkspace(t.id);
			if (p?.tenant && p.tenant.status !== "active") {
				await navigate({ to: "/status" });
				return;
			}
			toast.success(fill(en.shell.switched, { tenant: t.name }));
			await navigate({ to: "/" });
		} catch (e) {
			const err = toApiError(e);
			toast.error(messageFor(err).title);
			if (err.code === "not_member") await reloadPrincipal().catch(() => {});
		}
	};
}

function Avatar({ t }: { t: TokenTenant }) {
	return (
		<span
			aria-hidden
			className="grid size-6 shrink-0 place-items-center rounded-[6px] text-[10px] font-bold text-white"
			style={{ background: kbColor(t.id) }}
		>
			{initials(t.name)}
		</span>
	);
}

/** Inside the account menu (only when the user belongs to more than one workspace). */
export function WorkspaceMenuItems() {
	const principal = usePrincipal();
	const switchTo = useSwitchWorkspace();
	if (principal?.auth !== "session" || principal.tenants.length <= 1) return null;
	return (
		<>
			<DropdownMenuLabel className="nav-label">{en.shell.workspaces}</DropdownMenuLabel>
			{principal.tenants.map((t) => {
				const current = t.id === principal.tenant?.id;
				return (
					<DropdownMenuItem key={t.id} disabled={current} onSelect={() => void switchTo(t)}>
						<Avatar t={t} />
						<span className="min-w-0 flex-1 truncate">
							{t.name}
							<small className="block text-xs text-muted-foreground">
								{t.role === "owner" ? "Owner" : "Member"}
								{t.status !== "active" ? ` · ${t.status}` : ""}
							</small>
						</span>
						{current ? <Check aria-label="Current workspace" /> : null}
					</DropdownMenuItem>
				);
			})}
		</>
	);
}

/** On the status page: other ACTIVE workspaces to switch to. */
export function WorkspaceList() {
	const principal = usePrincipal();
	const switchTo = useSwitchWorkspace();
	const others = principal?.tenants.filter((t) => t.id !== principal.tenant?.id && t.status === "active") ?? [];
	if (others.length === 0) return null;
	return (
		<div className="grid w-full gap-2">
			<b className="text-[13px]">{en.status.switchTo}</b>
			{others.map((t) => (
				<Button key={t.id} className="justify-start" onClick={() => void switchTo(t)}>
					<Avatar t={t} />
					{t.name}
				</Button>
			))}
		</div>
	);
}
