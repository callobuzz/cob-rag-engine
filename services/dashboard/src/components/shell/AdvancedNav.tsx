import { useRouterState } from "@tanstack/react-router";
import { ChevronDown, SlidersHorizontal } from "lucide-react";
import { type ReactNode, useEffect, useId, useState } from "react";
import { readPref, writePref } from "@/lib/auth/storage";
import type { PageMeta } from "@/lib/nav";
import { en } from "@/lib/strings/en";
import { cn } from "@/lib/utils";

/** Where the person's open/closed choice is remembered (this browser only). */
export const ADVANCED_NAV_KEY = "cob.nav.advanced";

function isUnder(pathname: string, to: string): boolean {
	return pathname === to || pathname.startsWith(`${to}/`);
}

/**
 * The business menu's collapsed "Advanced" group (BIZ3): everything technical, folded away until
 * someone asks. It stays open on a page that lives inside it, so the current page is never hidden.
 * The choice is remembered per browser; storage may be unavailable, and then it simply starts shut.
 */
export function AdvancedNav({
	pages,
	collapsed,
	render,
}: {
	pages: readonly PageMeta[];
	collapsed: boolean;
	render: (page: PageMeta) => ReactNode;
}) {
	const panelId = useId();
	const pathname = useRouterState({ select: (s) => s.location.pathname });
	const inside = pages.some((p) => isUnder(pathname, p.path));
	const [open, setOpen] = useState(() => readPref(ADVANCED_NAV_KEY) === "1" || inside);

	useEffect(() => {
		if (inside) setOpen(true);
	}, [inside]);

	if (pages.length === 0) return null;
	const toggle = () => {
		const next = !open;
		setOpen(next);
		writePref(ADVANCED_NAV_KEY, next ? "1" : "0");
	};

	return (
		<div className="grid gap-0.5">
			{collapsed ? <div className="mx-3 my-2 border-t border-border" /> : null}
			<button
				type="button"
				onClick={toggle}
				aria-expanded={open}
				aria-controls={panelId}
				aria-label={collapsed ? en.shell.advanced : undefined}
				title={collapsed ? en.shell.advanced : undefined}
				className={cn(
					"mt-2 flex h-[34px] items-center gap-2.5 rounded-sm px-2.5 font-medium text-foreground-2 hover:bg-accent hover:text-foreground",
					collapsed && "justify-center px-0",
				)}
			>
				<SlidersHorizontal className="size-[18px] shrink-0" aria-hidden />
				{collapsed ? null : (
					<>
						<span className="truncate">{en.shell.advanced}</span>
						<ChevronDown
							className={cn("ml-auto size-4 shrink-0 transition-transform", open ? "rotate-180" : "rotate-0")}
							aria-hidden
						/>
					</>
				)}
			</button>
			<div id={panelId} hidden={!open} className="grid gap-0.5">
				{open ? pages.map((p) => render(p)) : null}
			</div>
		</div>
	);
}
