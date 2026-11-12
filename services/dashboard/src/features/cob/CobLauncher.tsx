import { useQuery } from "@tanstack/react-query";
import { useRouterState } from "@tanstack/react-router";
import { MessageCircle } from "lucide-react";
import { type KeyboardEvent, useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { useSelectedKb } from "@/components/shell/KbSwitcher";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { onCobOpen } from "@/lib/cob-bus";
import { cobStrings as t } from "@/lib/strings/cob";
import { cn } from "@/lib/utils";
import { CONV_KEY, useCobChat } from "./api";
import { CobPanel } from "./CobPanel";
import { isMac, useCobShortcut } from "./useCobShortcut";
import { useResizableWidth } from "./useResizableWidth";

const DESKTOP = "(min-width: 861px)";

function useDesktop(): boolean {
	return useSyncExternalStore(
		(cb) => {
			const mql = window.matchMedia?.(DESKTOP);
			mql?.addEventListener?.("change", cb);
			return () => mql?.removeEventListener?.("change", cb);
		},
		() => window.matchMedia?.(DESKTOP).matches === true,
		() => false,
	);
}

/**
 * The COB button (bottom-right, every signed-in screen) and the panel it opens. Not rendered when
 * COB is switched off on the engine, or for an API-key sign-in (COB is for people with a session).
 * Mounted once, by `AppShell`.
 */
export function CobLauncher() {
	const info = useQuery(infoQuery);
	const principal = usePrincipal();
	const enabled = info.data?.features?.cob?.enabled === true && principal?.auth === "session";
	// A view or workspace switch starts a clean chat: the remembered conversation is another scope's.
	const scope = `${principal?.view}:${principal?.tenant?.id ?? "admin"}`;
	const lastScope = useRef(scope);
	if (lastScope.current !== scope) {
		lastScope.current = scope;
		try {
			window.sessionStorage.removeItem(CONV_KEY);
		} catch {
			// Storage blocked: nothing was remembered.
		}
	}
	return enabled ? <CobDock key={scope} /> : null;
}

function CobDock() {
	const [open, setOpen] = useState(false);
	const openRef = useRef(false);
	const desktop = useDesktop();
	const { width, handleProps } = useResizableWidth();
	const pathname = useRouterState({ select: (s) => s.location.pathname });
	const { selected } = useSelectedKb();
	const [docsSlug, setDocsSlug] = useState<string | null>(null);
	const [prefill, setPrefill] = useState<{ text: string; nonce: number } | null>(null);
	const chat = useCobChat({ route: pathname, kbId: selected?.id ?? null, docsSlug });
	const inputRef = useRef<HTMLTextAreaElement | null>(null);
	const launcherRef = useRef<HTMLButtonElement>(null);
	const opener = useRef<HTMLElement | null>(null);

	// A Docs page slug belongs to the page it was asked on.
	// biome-ignore lint/correctness/useExhaustiveDependencies: the path change is the trigger
	useEffect(() => {
		setDocsSlug(null);
	}, [pathname]);

	const openPanel = useCallback(() => {
		if (openRef.current) return;
		openRef.current = true;
		opener.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
		setOpen(true);
	}, []);

	const closePanel = useCallback(() => {
		if (!openRef.current) return;
		openRef.current = false;
		setOpen(false);
		setTimeout(() => {
			const back = opener.current?.isConnected ? opener.current : launcherRef.current;
			back?.focus();
		}, 0);
	}, []);

	const toggle = useCallback(() => (openRef.current ? closePanel() : openPanel()), [openPanel, closePanel]);
	useCobShortcut(toggle);

	const send = chat.send;
	useEffect(
		() =>
			onCobOpen((req) => {
				if (req.docsSlug) setDocsSlug(req.docsSlug);
				openPanel();
				if (!req.prompt) return;
				if (req.send) void send(req.prompt, { docsSlug: req.docsSlug ?? null });
				else setPrefill({ text: req.prompt, nonce: Date.now() });
			}),
		[openPanel, send],
	);

	// Desktop: the page makes room for the docked panel through one CSS variable (AppShell reads it).
	useEffect(() => {
		const root = document.documentElement;
		root.style.setProperty("--cob-w", open && desktop ? `${width}px` : "0px");
		return () => {
			root.style.removeProperty("--cob-w");
		};
	}, [open, desktop, width]);

	// Desktop: focus moves into the composer on open (the phone sheet does it in onOpenAutoFocus).
	useEffect(() => {
		if (open && desktop) inputRef.current?.focus();
	}, [open, desktop]);

	const onAsideKey = (e: KeyboardEvent<HTMLElement>) => {
		if (e.key === "Escape" && !e.defaultPrevented) {
			e.stopPropagation();
			closePanel();
		}
	};

	const panel = <CobPanel chat={chat} route={pathname} onClose={closePanel} inputRef={inputRef} prefill={prefill} />;

	return (
		<>
			<button
				ref={launcherRef}
				type="button"
				aria-label={isMac() ? t.launcherMac : t.launcher}
				aria-expanded={open}
				aria-haspopup={desktop ? undefined : "dialog"}
				aria-controls={open && desktop ? "cob-panel" : undefined}
				data-testid="cob-launcher"
				onClick={toggle}
				className={cn(
					"fixed bottom-4 z-40 inline-flex h-11 items-center gap-2 rounded-full border border-transparent bg-brand px-4 text-[13.5px] font-bold text-brand-foreground shadow-lg outline-none",
					"right-4 min-[861px]:right-[calc(var(--cob-w,0px)+16px)] hover:brightness-110 focus-visible:ring-[3px] focus-visible:ring-ring/50",
				)}
			>
				<MessageCircle className="size-[18px]" aria-hidden />
				{t.name}
			</button>

			{open && desktop ? (
				<aside
					id="cob-panel"
					aria-label={t.panel}
					data-testid="cob-panel"
					style={{ width }}
					onKeyDown={onAsideKey}
					className="fixed top-0 right-0 z-40 flex h-dvh border-l border-border bg-background shadow-xl"
				>
					<div
						{...handleProps}
						className="absolute top-0 left-0 z-10 h-full w-2 -translate-x-1/2 cursor-col-resize touch-none outline-none hover:bg-brand/40 focus-visible:bg-brand/60"
					/>
					<div className="min-w-0 flex-1">{panel}</div>
				</aside>
			) : null}

			<Sheet
				open={open && !desktop}
				onOpenChange={(o) => {
					if (!o) closePanel();
				}}
			>
				<SheetContent
					side="right"
					showCloseButton={false}
					data-testid="cob-sheet"
					className="w-full max-w-none gap-0 p-0 sm:max-w-none"
					onOpenAutoFocus={(e) => {
						e.preventDefault();
						inputRef.current?.focus();
					}}
					onCloseAutoFocus={(e) => e.preventDefault()}
				>
					<SheetTitle className="sr-only">{t.panel}</SheetTitle>
					<SheetDescription className="sr-only">{t.greeting}</SheetDescription>
					<div className="min-h-0 flex-1">{panel}</div>
				</SheetContent>
			</Sheet>
		</>
	);
}
