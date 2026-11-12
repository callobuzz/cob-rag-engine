import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import type { LucideIcon } from "lucide-react";
import { Boxes, KeyRound, Monitor, Moon, Sun, Upload, UserPlus, Users } from "lucide-react";
import { useEffect, useSyncExternalStore } from "react";
import {
	CommandDialog,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
} from "@/components/ui/command";
import { KbAvatar } from "@/features/kbs/KbAvatar";
import { corporaSwitcherQuery, infoQuery } from "@/lib/api/queries";
import type { Principal } from "@/lib/auth/principal";
import { principalKey } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { useDashboardFeatures } from "@/lib/features"; // BIZ3
import { rememberKb } from "@/lib/kb-selection";
import { pageLabel, visiblePages } from "@/lib/nav"; // BIZ3
import { can } from "@/lib/permissions";
import { en } from "@/lib/strings/en";
import { setTheme } from "@/lib/theme";

/**
 * Ctrl/⌘+K palette (mockup `.palette`, cmdk): Pages (role-filtered), Knowledge bases, Actions.
 * Later epics add actions with `registerCommand()`; each command decides who sees it.
 */
export interface PaletteCommand {
	id: string;
	label: string;
	icon?: LucideIcon;
	/** Who sees it: omitted = everyone signed in. */
	visible?: (p: Principal) => boolean;
	run: (ctx: { navigate: ReturnType<typeof useNavigate> }) => void;
}

const registry = new Map<string, PaletteCommand>();
const listeners = new Set<() => void>();
let snapshot: PaletteCommand[] = [];

export function registerCommand(cmd: PaletteCommand): () => void {
	registry.set(cmd.id, cmd);
	snapshot = [...registry.values()];
	for (const l of listeners) l();
	return () => {
		registry.delete(cmd.id);
		snapshot = [...registry.values()];
		for (const l of listeners) l();
	};
}

function useCommands(): PaletteCommand[] {
	return useSyncExternalStore(
		(l) => {
			listeners.add(l);
			return () => listeners.delete(l);
		},
		() => snapshot,
		() => snapshot,
	);
}

const ownersOnly = (p: Principal) => can(p, "widgetKey.manage");

// Built-in actions (mockup CMDS). Later epics replace `run` with the real dialogs.
const BUILT_IN: PaletteCommand[] = [
	{ id: "theme-light", label: "Switch to light theme", icon: Sun, run: () => setTheme("light") },
	{ id: "theme-dark", label: "Switch to dark theme", icon: Moon, run: () => setTheme("dark") },
	{ id: "theme-system", label: "Use the system theme", icon: Monitor, run: () => setTheme("system") },
	{ id: "upload", label: "Upload files", icon: Upload, run: ({ navigate }) => void navigate({ to: "/add" }) },
	{
		id: "widget-key",
		label: "Create a widget key",
		icon: Boxes,
		visible: ownersOnly,
		run: ({ navigate }) => void navigate({ to: "/widget" }),
	},
	{
		id: "rotate-kb-key",
		label: "Rotate this knowledge base's key",
		icon: KeyRound,
		visible: ownersOnly,
		run: ({ navigate }) => void navigate({ to: "/kbs" }),
	},
	{
		id: "invite",
		label: "Invite a teammate",
		icon: UserPlus,
		visible: (p) => p.view === "tenant" && can(p, "team.manage"),
		run: ({ navigate }) => void navigate({ to: "/team", search: { invite: 1 } }),
	},
	{
		id: "activate-pending",
		label: "Activate pending sign-ups",
		icon: Users,
		visible: (p) => p.view === "admin",
		run: ({ navigate }) => void navigate({ to: "/admin/customers", search: { status: "pending" } }),
	},
];
for (const c of BUILT_IN) registry.set(c.id, c);
snapshot = [...registry.values()];

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
	const principal = usePrincipal();
	const navigate = useNavigate();
	const info = useQuery(infoQuery);
	const corpora = useQuery({ ...corporaSwitcherQuery, enabled: open });
	const commands = useCommands();
	const dashboard = useDashboardFeatures(); // BIZ3

	useEffect(() => {
		const onKey = (e: KeyboardEvent) => {
			if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
				e.preventDefault();
				onOpenChange(!open);
			}
		};
		window.addEventListener("keydown", onKey);
		return () => window.removeEventListener("keydown", onKey);
	}, [open, onOpenChange]);

	if (!principal) return null;
	const pages = visiblePages(
		principal,
		{ content_portal: info.data?.features.content_portal, whatsapp: info.data?.features.whatsapp?.available },
		dashboard,
	).filter((p) => p.group !== "Hidden" || p.id === "profile" || p.id === "conversations");
	const visibleCommands = commands.filter((c) => !c.visible || c.visible(principal));
	const close = () => onOpenChange(false);

	return (
		<CommandDialog
			open={open}
			onOpenChange={onOpenChange}
			title={en.palette.label}
			description={en.palette.placeholder}
			showCloseButton={false}
			className="bg-card sm:max-w-[620px]"
		>
			<CommandInput placeholder={en.palette.placeholder} aria-label={en.palette.placeholder} />
			<CommandList className="max-h-[380px]">
				<CommandEmpty>{en.palette.empty}</CommandEmpty>
				<CommandGroup heading={en.palette.pages}>
					{pages
						.filter((p) => !(p.id === "profile" && principal.auth !== "session"))
						.map((p) => (
							<CommandItem
								key={p.id}
								value={`page ${pageLabel(p, principal.view)}`}
								onSelect={() => {
									close();
									// TV6: /whatsapp has its own CSP, which only a fresh page load picks up
									if (p.path === "/whatsapp") window.location.assign("/whatsapp");
									else void navigate({ to: p.path });
								}}
							>
								<p.icon aria-hidden />
								{pageLabel(p, principal.view)}
								<span className="ml-auto text-xs text-muted-foreground">page</span>
							</CommandItem>
						))}
				</CommandGroup>
				{corpora.data && corpora.data.items.length > 0 ? (
					<CommandGroup heading={en.palette.kbs}>
						{corpora.data.items.map((k) => (
							<CommandItem
								key={k.id}
								value={`kb ${k.name} ${k.tenant_name}`}
								onSelect={() => {
									close();
									rememberKb(principalKey(principal), k.id);
									void navigate({ to: "/kbs/$kbId", params: { kbId: k.id } });
								}}
							>
								<KbAvatar kb={k} size={20} />
								{k.name}
								{principal.view === "admin" ? <span className="text-muted-foreground">· {k.tenant_name}</span> : null}
							</CommandItem>
						))}
					</CommandGroup>
				) : null}
				<CommandGroup heading={en.palette.actions}>
					{visibleCommands.map((c) => (
						<CommandItem
							key={c.id}
							value={`action ${c.label}`}
							onSelect={() => {
								close();
								c.run({ navigate });
							}}
						>
							{c.icon ? <c.icon aria-hidden /> : null}
							{c.label}
							<span className="ml-auto text-xs text-muted-foreground">action</span>
						</CommandItem>
					))}
				</CommandGroup>
			</CommandList>
			<div className="flex gap-3.5 border-t border-border px-3.5 py-2 text-xs text-muted-foreground">
				<span>
					<kbd className="kbd">↑</kbd> <kbd className="kbd">↓</kbd> {en.palette.move}
				</span>
				<span>
					<kbd className="kbd">Enter</kbd> {en.palette.open}
				</span>
				<span>
					<kbd className="kbd">Esc</kbd> {en.palette.close}
				</span>
			</div>
		</CommandDialog>
	);
}
