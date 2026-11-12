import { useNavigate } from "@tanstack/react-router";
import { ArrowLeftRight, Check, House, ShieldCheck } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuLabel,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { reloadPrincipal, switchToAdmin, switchWorkspace } from "@/lib/auth/actions";
import { inOperatorWorkspace, operatorWorkspace } from "@/lib/auth/principal";
import { getAuthState, setAuthState, usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { en, fill } from "@/lib/strings/en";

export type ScopeTarget = "operator" | "workspace";
/** Where to land after the switch: the scope's home, or a step of the getting-started list. */
export type ScopeDest = "home" | "newKb" | "widget";

/**
 * Switch between the operator view and the super admin's own workspace. Calls
 * `POST /auth/switch-tenant` (into the one operator workspace) or `POST /auth/switch-admin`
 * (back); the auth layer swaps the access token, clears the query cache and reloads the principal.
 * `switching` keeps the shell from rendering a page of the scope being left with the new token.
 * Resolves true when the switch happened.
 */
export function useSwitchScope() {
	const navigate = useNavigate();
	const { productName } = useBranding();
	return async (target: ScopeTarget, dest: ScopeDest = "home"): Promise<boolean> => {
		const workspace = operatorWorkspace(getAuthState().principal);
		if (target === "workspace" && !workspace) return false;
		setAuthState({ switching: true });
		try {
			const p = target === "workspace" && workspace ? await switchWorkspace(workspace.id) : await switchToAdmin();
			if (p?.tenant && p.tenant.status !== "active") {
				await navigate({ to: "/status" });
				return true;
			}
			if (target === "workspace" && dest === "newKb") await navigate({ to: "/kbs", search: { new: 1 } });
			else if (target === "workspace" && dest === "widget") await navigate({ to: "/widget" });
			else await navigate({ to: "/" });
			toast.success(
				target === "workspace"
					? fill(en.shell.scope.switchedWorkspace, { product: productName })
					: en.shell.scope.switchedOperator,
			);
			return true;
		} catch (e) {
			const err = toApiError(e);
			toast.error(messageFor(err).title);
			if (err.code === "not_member") await reloadPrincipal().catch(() => {});
			return false;
		} finally {
			setAuthState({ switching: false });
		}
	};
}

/** The two views as menu items: "Operator view" and "<product> workspace (yours)". */
export function ScopeMenuItems() {
	const principal = usePrincipal();
	const { productName } = useBranding();
	const switchScope = useSwitchScope();
	if (!operatorWorkspace(principal)) return null;
	const inWorkspace = inOperatorWorkspace(principal);
	const s = en.shell.scope;
	return (
		<>
			<DropdownMenuLabel className="nav-label">{s.label}</DropdownMenuLabel>
			<DropdownMenuItem disabled={!inWorkspace} onSelect={() => void switchScope("operator")}>
				<ShieldCheck aria-hidden />
				<span className="min-w-0 flex-1">
					{s.operator}
					<small className="block text-xs text-muted-foreground">{s.operatorHint}</small>
				</span>
				{inWorkspace ? null : <Check aria-label={s.current} />}
			</DropdownMenuItem>
			<DropdownMenuItem disabled={inWorkspace} onSelect={() => void switchScope("workspace")}>
				<House aria-hidden />
				<span className="min-w-0 flex-1">
					{fill(s.workspace, { product: productName })}
					<small className="block text-xs text-muted-foreground">{s.workspaceHint}</small>
				</span>
				{inWorkspace ? <Check aria-label={s.current} /> : null}
			</DropdownMenuItem>
		</>
	);
}

/** Top-bar control for the super admin; renders nothing for anyone who has no workspace to switch to. */
export function ScopeSwitcher() {
	const principal = usePrincipal();
	const { productName } = useBranding();
	if (!operatorWorkspace(principal)) return null;
	const inWorkspace = inOperatorWorkspace(principal);
	const s = en.shell.scope;
	const label = inWorkspace ? fill(s.workspaceShort, { product: productName }) : s.operator;
	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button
					variant="outline"
					size="sm"
					data-testid="scope-switcher"
					aria-label={`${s.label}: ${label}`}
					className="h-9 shrink-0 gap-2 rounded-full px-3"
				>
					{inWorkspace ? <House aria-hidden /> : <ShieldCheck aria-hidden />}
					<span className="hidden max-w-[180px] truncate min-[861px]:inline">{label}</span>
					<ArrowLeftRight className="size-3.5 text-muted-foreground" aria-hidden />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end" className="min-w-[300px]">
				<ScopeMenuItems />
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
