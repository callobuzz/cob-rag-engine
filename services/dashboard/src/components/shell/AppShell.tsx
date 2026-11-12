import { useRouterState } from "@tanstack/react-router";
import { type ReactNode, useEffect, useState } from "react";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { CobLauncher } from "@/features/cob/CobLauncher";
import { LegalLinks } from "@/features/legal/LegalLinks"; // P3 legal
import { readPref, writePref } from "@/lib/auth/storage";
import { useAuth } from "@/lib/auth/store";
import { en } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { BillingBanner } from "./BillingBanner"; // BIL4
import { CommandPalette } from "./CommandPalette";
import { DemoBanner } from "./DemoBanner"; // L5a demo
import { FairUseBanner } from "./FairUseBanner"; // PIP4
import { PoweredBy } from "./PoweredBy";
import { Sidebar } from "./Sidebar";
import { Topbar } from "./Topbar";

/**
 * Signed-in shell (mockup `.app`): sidebar 248 px (collapsible to 64 px, localStorage
 * ["cob.sidebar"]), drawer below 861 px, top bar, content max 1280 px, PoweredBy at the bottom
 * of the content scroll area on every page.
 *
 * Layout contract (app-shell pattern): the shell is exactly one viewport tall (`h-dvh`) and clips
 * (`overflow-hidden`), so the document never scrolls while signed in; `#content` is the ONE scroll
 * region at every width and the sidebar scrolls only its own nav if that overflows.
 *
 * Two details carry it. `relative`: an absolutely positioned descendant (every `sr-only` label)
 * is only clipped by an overflow ancestor when that ancestor is its containing block, otherwise it
 * stays at its static position far down the page and stretches the document, which is what made
 * the empty area below the shell scroll and carry the sidebar and footer away. And the single grid
 * row is `minmax(0, 1fr)`: with an implicit `auto` row the grid can grow with its content.
 */
export function AppShell({ children }: { children: ReactNode }) {
	const [collapsed, setCollapsed] = useState(() => readPref("cob.sidebar") === "collapsed");
	const [drawer, setDrawer] = useState(false);
	const [palette, setPalette] = useState(false);
	const pathname = useRouterState({ select: (s) => s.location.pathname });
	const { switching } = useAuth();

	useEffect(() => {
		// Close the drawer after navigating (pathname is the trigger). Scrolling #content to the top
		// on a new page is the router's scrollToTopSelectors.
		void pathname;
		setDrawer(false);
	}, [pathname]);

	const toggle = () => {
		setCollapsed((c) => {
			writePref("cob.sidebar", c ? "expanded" : "collapsed");
			return !c;
		});
	};

	return (
		<div
			className={cn(
				"relative grid h-dvh grid-cols-1 grid-rows-[minmax(0,1fr)] overflow-hidden bg-background transition-[grid-template-columns] duration-[var(--t)]",
				collapsed ? "min-[861px]:grid-cols-[64px_minmax(0,1fr)]" : "min-[861px]:grid-cols-[248px_minmax(0,1fr)]",
			)}
		>
			<a
				href="#main"
				className="sr-only focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 focus:rounded-sm focus:bg-card focus:px-3 focus:py-2"
			>
				{en.common.skipToContent}
			</a>
			<aside className="hidden min-h-0 border-r border-border min-[861px]:block">
				<Sidebar collapsed={collapsed} onToggleCollapse={toggle} />
			</aside>
			<Sheet open={drawer} onOpenChange={setDrawer}>
				<SheetContent
					side="left"
					className="w-[272px] max-w-[85vw] gap-0 border-r-border bg-muted p-0 [&>button]:hidden"
				>
					<SheetTitle className="sr-only">{en.shell.mainNav}</SheetTitle>
					<SheetDescription className="sr-only">{en.shell.mainNav}</SheetDescription>
					<Sidebar inDrawer onNavigate={() => setDrawer(false)} />
				</SheetContent>
			</Sheet>
			<div className="flex min-h-0 min-w-0 flex-col min-[861px]:mr-[var(--cob-w,0px)]">
				<Topbar onOpenMenu={() => setDrawer(true)} onOpenPalette={() => setPalette(true)} />
				<DemoBanner /> {/* L5a demo */}
				<BillingBanner /> {/* BIL4 */}
				<FairUseBanner /> {/* PIP4 */}
				<div className="min-h-0 flex-1 overflow-y-auto overscroll-contain" id="content">
					<main
						id="main"
						tabIndex={-1}
						className="mx-auto grid max-w-[1280px] gap-5 px-4 pt-4 pb-2 outline-none min-[861px]:px-7 min-[861px]:pt-6"
					>
						{switching ? (
							<div role="status" aria-busy="true" className="grid gap-4">
								<span className="sr-only">{en.shell.scope.switching}</span>
								<CardSkeleton lines={1} />
								<CardSkeleton lines={4} />
							</div>
						) : (
							children
						)}
					</main>
					{/* P3 legal */}
					<LegalLinks />
					{/* /P3 legal */}
					<PoweredBy />
				</div>
			</div>
			<CommandPalette open={palette} onOpenChange={setPalette} />
			<CobLauncher />
		</div>
	);
}
