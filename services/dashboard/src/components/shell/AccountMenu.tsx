import { useNavigate } from "@tanstack/react-router";
import { ChevronsUpDown, CreditCard, LogOut, User } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { signOut } from "@/lib/auth/actions";
import { displayName, firstName, initials, operatorWorkspace, type Principal, roleBadge } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { en, fill } from "@/lib/strings/en";
import { ScopeMenuItems } from "./ScopeSwitcher";
import { WorkspaceMenuItems } from "./WorkspaceSwitcher";

export function PersonAvatar({ principal, size = 28 }: { principal: Principal; size?: number }) {
	return (
		<span
			aria-hidden
			style={{ width: size, height: size }}
			className="grid shrink-0 place-items-center rounded-full bg-[linear-gradient(135deg,#f2b441,#ff7a85)] text-[11.5px] font-bold text-[#1b1200]"
		>
			{initials(displayName(principal))}
		</span>
	);
}

/** Mockup `#acctPop`. */
export function AccountMenu() {
	const principal = usePrincipal();
	const navigate = useNavigate();
	if (!principal) return null;
	const secondLine = principal.user
		? principal.user.email
		: fill(en.shell.apiKeyLabel, { kind: principal.keyKind === "admin" ? en.shell.adminKey : en.shell.tenantKey });
	const badgeVariant = principal.view === "admin" ? "warn" : principal.role === "owner" ? "info" : "default";
	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<button
					type="button"
					aria-label={`${en.shell.account}: ${displayName(principal)}`}
					className="flex h-9 items-center gap-2 rounded-full border border-border bg-card pr-1.5 pl-1 hover:bg-accent"
				>
					<PersonAvatar principal={principal} />
					<span className="hidden text-[13px] font-semibold min-[861px]:inline">{firstName(principal)}</span>
					<ChevronsUpDown className="size-3.5 text-muted-foreground" aria-hidden />
				</button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end" className="min-w-[280px]">
				<div className="flex items-center gap-2.5 px-2 pt-2 pb-2.5">
					<PersonAvatar principal={principal} size={32} />
					<div className="min-w-0">
						<b className="block truncate">{displayName(principal)}</b>
						<small className="block truncate text-xs text-muted-foreground">{secondLine}</small>
						<Badge variant={badgeVariant} className="mt-1">
							{roleBadge(principal)}
						</Badge>
					</div>
				</div>
				<DropdownMenuSeparator />
				{principal.auth === "session" ? (
					<DropdownMenuItem onSelect={() => void navigate({ to: "/profile" })}>
						<User aria-hidden /> {en.shell.profile}
					</DropdownMenuItem>
				) : null}
				{principal.view === "tenant" ? (
					<DropdownMenuItem onSelect={() => void navigate({ to: "/plan" })}>
						<CreditCard aria-hidden /> {en.shell.planUsage}
					</DropdownMenuItem>
				) : null}
				{operatorWorkspace(principal) ? (
					<>
						<DropdownMenuSeparator />
						<ScopeMenuItems />
					</>
				) : null}
				{principal.auth === "session" && principal.tenants.length > 1 ? (
					<>
						<DropdownMenuSeparator />
						<WorkspaceMenuItems />
					</>
				) : null}
				<DropdownMenuSeparator />
				<DropdownMenuItem onSelect={() => void signOut(null)}>
					<LogOut aria-hidden /> {en.common.logOut}
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
