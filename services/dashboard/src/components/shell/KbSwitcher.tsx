import { useQuery } from "@tanstack/react-query";
import { useLocation, useNavigate } from "@tanstack/react-router";
import { ChevronsUpDown, Database } from "lucide-react";
import { useMemo, useState } from "react";
import {
	Command,
	CommandEmpty,
	CommandGroup,
	CommandInput,
	CommandItem,
	CommandList,
	CommandSeparator,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Skeleton } from "@/components/ui/skeleton";
import { KbAvatar } from "@/features/kbs/KbAvatar";
import { type Corpus, corporaSwitcherQuery, KB_SWITCHER_LIMIT } from "@/lib/api/queries";
import { principalKey } from "@/lib/auth/principal";
import { usePrincipal } from "@/lib/auth/store";
import { isKbScoped, rememberKb, useRememberedKb } from "@/lib/kb-selection";
import { en } from "@/lib/strings/en";
import { cn } from "@/lib/utils";

/** Selected KB: `?kb=` on a KB-scoped route, else the remembered one, else the first by name. */
export function useSelectedKb(): { kbs: Corpus[]; selected: Corpus | null } {
	const principal = usePrincipal();
	const location = useLocation();
	const remembered = useRememberedKb(principal ? principalKey(principal) : null);
	const q = useQuery(corporaSwitcherQuery);
	const kbs = useMemo(() => [...(q.data?.items ?? [])].sort((a, b) => a.name.localeCompare(b.name, "en-IN")), [q.data]);
	const fromUrl = (location.search as { kb?: string }).kb;
	const selected = kbs.find((k) => k.id === fromUrl) ?? kbs.find((k) => k.id === remembered) ?? kbs[0] ?? null;
	return { kbs, selected };
}

/** Mockup `#kbBtn` / `#kbPop`. */
export function KbSwitcher() {
	const principal = usePrincipal();
	const navigate = useNavigate();
	const location = useLocation();
	const q = useQuery(corporaSwitcherQuery);
	const { kbs, selected } = useSelectedKb();
	const [open, setOpen] = useState(false);
	const isAdmin = principal?.view === "admin";

	const groups = useMemo(() => {
		const map = new Map<string, Corpus[]>();
		for (const k of kbs) {
			const g = isAdmin ? k.tenant_name : (principal?.tenant?.name ?? k.tenant_name);
			map.set(g, [...(map.get(g) ?? []), k]);
		}
		return [...map.entries()].sort(([a], [b]) => a.localeCompare(b, "en-IN"));
	}, [kbs, isAdmin, principal]);

	if (q.isPending) return <Skeleton className="h-9 w-44" />;

	const single = kbs.length === 1;
	const choose = (kb: Corpus) => {
		setOpen(false);
		if (principal) rememberKb(principalKey(principal), kb.id);
		if (isKbScoped(location.pathname)) {
			void navigate({ to: ".", search: (prev: Record<string, unknown>) => ({ ...prev, kb: kb.id }) });
		}
	};

	return (
		<Popover open={open} onOpenChange={setOpen}>
			<PopoverTrigger asChild>
				<button
					type="button"
					disabled={single || (kbs.length === 0 && !q.isError)}
					aria-haspopup="listbox"
					aria-expanded={open}
					aria-label={`${en.shell.kbSwitcher}: ${selected?.name ?? (q.isError ? en.shell.kbLoadError : en.shell.kbNone)}`}
					className="flex h-9 min-w-0 items-center gap-2.5 rounded-sm border border-border bg-card pr-2.5 pl-1.5 font-semibold hover:bg-accent disabled:cursor-default disabled:hover:bg-card"
				>
					{selected ? <KbAvatar kb={selected} /> : <Database className="size-4 text-muted-foreground" aria-hidden />}
					<span className="max-w-[110px] truncate min-[521px]:max-w-[220px]">
						{selected?.name ?? (q.isError ? en.shell.kbLoadError : en.shell.kbNone)}
					</span>
					{selected && isAdmin ? (
						<span className="hidden truncate text-xs font-medium text-muted-foreground min-[861px]:inline">
							· {selected.tenant_name}
						</span>
					) : null}
					{single ? null : <ChevronsUpDown className="size-[15px] text-muted-foreground" aria-hidden />}
				</button>
			</PopoverTrigger>
			<PopoverContent align="start" className="w-[320px] p-0">
				<Command>
					<CommandInput placeholder={en.shell.kbSearch} aria-label={en.shell.kbSearch} />
					<CommandList>
						{q.isError ? (
							<div className="grid gap-2 p-4 text-[13px]">
								<span className="text-destructive">{en.shell.kbLoadError}</span>
								<button
									type="button"
									className="justify-self-start font-semibold text-primary"
									onClick={() => void q.refetch()}
								>
									{en.common.retry}
								</button>
							</div>
						) : null}
						<CommandEmpty>{en.palette.empty}</CommandEmpty>
						{groups.map(([tenant, items]) => (
							<CommandGroup key={tenant} heading={tenant}>
								{items.map((k) => (
									<CommandItem
										key={k.id}
										value={`${k.name} ${k.tenant_name} ${k.id}`}
										onSelect={() => choose(k)}
										className={cn(k.id === selected?.id && "bg-accent")}
										aria-selected={k.id === selected?.id}
									>
										<KbAvatar kb={k} />
										<span className="truncate">{k.name}</span>
									</CommandItem>
								))}
							</CommandGroup>
						))}
						<CommandSeparator />
						<CommandGroup>
							<CommandItem
								value="manage knowledge bases"
								onSelect={() => {
									setOpen(false);
									void navigate({ to: "/kbs" });
								}}
							>
								<Database aria-hidden /> {en.shell.kbManage}
							</CommandItem>
						</CommandGroup>
						{(q.data?.total ?? 0) > KB_SWITCHER_LIMIT ? (
							<p className="m-0 px-3 py-2 text-xs text-muted-foreground">{en.shell.kbFirst500}</p>
						) : null}
					</CommandList>
				</Command>
			</PopoverContent>
		</Popover>
	);
}
