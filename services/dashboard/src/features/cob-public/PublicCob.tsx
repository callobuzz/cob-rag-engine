import { ExternalLink, MessageCircle, MessageSquarePlus, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { AnimatedLogo } from "@/components/brand/AnimatedLogo";
import { RetryAfter, useCountdown } from "@/components/patterns/RetryAfter";
import { PoweredBy } from "@/components/shell/PoweredBy";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { Markdown } from "@/lib/markdown/Markdown";
import { cobStrings as cob } from "@/lib/strings/cob";
import { cobPublicStrings as t } from "@/lib/strings/cob-public";
import { fill } from "@/lib/strings/en";
import { CobComposer } from "../cob/CobComposer";
import { CobLinkButton } from "../cob/CobLinkButton";
import { announcement } from "../cob/CobPanel";
import { CobStageLine } from "../cob/CobStageLine";
import { useCobLinks } from "../cob/links";
import type { CobLinkBlock, CobSearchResultsBlock, CobSource, CobTurnView } from "../cob/types";
import { type PublicCob as PublicCobChat, usePublicCob } from "./usePublicCob";

const CHIP =
	"inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-[12px] text-foreground-2 no-underline hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none";

/**
 * A visitor cannot open a signed-in screen or the in-app Docs: a source is its public page on the
 * project site, or a public screen of this dashboard (sign-in, sign-up…), or plain text.
 */
function PublicSources({ sources }: { sources: CobSource[] }) {
	const { onInternalClick } = useCobLinks();
	if (!sources.length) return null;
	return (
		<ul aria-label={t.sources.label} className="m-0 flex list-none flex-wrap gap-1.5 p-0" data-testid="cob-sources">
			{sources.map((s) => {
				const label = (
					<>
						<span className="font-semibold text-foreground">{s.index}</span>
						<span className="truncate">{s.title}</span>
					</>
				);
				return (
					<li key={`${s.index}-${s.slug}-${s.anchor}`} className="min-w-0 max-w-full">
						{s.public_url ? (
							<a
								href={s.public_url}
								target="_blank"
								rel="noopener noreferrer"
								data-source={s.index}
								aria-label={fill(t.sources.openPublic, { title: s.title })}
								className={CHIP}
							>
								{label}
								<ExternalLink className="size-3 shrink-0" aria-hidden />
							</a>
						) : s.route ? (
							<a
								href={s.route}
								data-source={s.index}
								aria-label={fill(t.sources.open, { title: s.title })}
								onClick={onInternalClick(s.route)}
								className={CHIP}
							>
								{label}
							</a>
						) : (
							<span data-source={s.index} tabIndex={-1} className={CHIP}>
								{label}
							</span>
						)}
					</li>
				);
			})}
		</ul>
	);
}

/** Search mode (no answer model reachable): the matching public pages. */
function PublicSearchResults({ block }: { block: CobSearchResultsBlock }) {
	return (
		<section className="grid gap-2" data-testid="cob-search-results" aria-label={t.sources.label}>
			<p
				role="note"
				className="m-0 rounded-md border border-border bg-secondary px-3 py-2 text-[12.5px] text-foreground-2"
			>
				{t.search.banner}
			</p>
			{block.items.length === 0 ? (
				<p className="m-0 text-[13px] text-muted-foreground">{t.search.empty}</p>
			) : (
				<ul className="m-0 grid list-none gap-2 p-0">
					{block.items.map((s) => (
						<li key={`${s.slug}#${s.anchor}`} className="grid gap-1 rounded-md border border-border bg-card p-3">
							<span className="text-[13px] font-semibold">{s.title}</span>
							<span className="text-[12.5px] text-foreground-2">{s.snippet}</span>
							{s.public_url ? (
								<a
									href={s.public_url}
									target="_blank"
									rel="noopener noreferrer"
									className="w-fit text-[12.5px] font-semibold text-brand-text underline-offset-4 hover:underline"
								>
									{t.search.open}
								</a>
							) : null}
						</li>
					))}
				</ul>
			)}
		</section>
	);
}

function PublicMessage({
	turn,
	latest,
	onRetry,
	limit,
}: {
	turn: CobTurnView;
	latest: boolean;
	onRetry: () => void;
	limit: PublicCobChat["limit"];
}) {
	const streaming = turn.status === "streaming";
	const syncing = turn.error?.code === "cob_docs_syncing";
	const waiting = useCountdown(latest ? (limit?.until ?? null) : null) > 0;
	const counting = waiting && limit?.scope === "ip";
	return (
		<article className="grid gap-3" data-testid="cob-turn" aria-label={turn.question}>
			<p className="m-0 ml-auto max-w-[85%] rounded-lg bg-secondary px-3 py-2 text-[13.5px] break-words whitespace-pre-wrap">
				<span className="sr-only">{t.you}: </span>
				{turn.question}
			</p>
			<div className="grid min-w-0 gap-2.5 text-[13.5px]">
				<span className="sr-only">{t.cob}: </span>
				{streaming && !turn.text ? <CobStageLine stage={turn.stage} /> : null}
				{turn.text ? <Markdown text={turn.text} streaming={streaming} /> : null}
				<PublicSources sources={turn.sources} />
				{turn.blocks.map((b, i) =>
					b.kind === "cob_link" ? (
						// biome-ignore lint/suspicious/noArrayIndexKey: blocks are append-only, in stream order
						<CobLinkButton key={i} block={b as CobLinkBlock} />
					) : b.kind === "cob_search_results" ? (
						// biome-ignore lint/suspicious/noArrayIndexKey: blocks are append-only, in stream order
						<PublicSearchResults key={i} block={b as CobSearchResultsBlock} />
					) : null,
				)}
				{turn.status === "error" ? (
					<div role="alert" className="grid gap-2 rounded-md border border-border bg-card p-3" data-testid="cob-error">
						<p className="m-0 text-[13px]">
							{counting ? (
								<RetryAfter until={limit?.until ?? null} template={t.rateLimited} />
							) : syncing ? (
								t.syncing
							) : (
								turn.error?.detail || t.generic
							)}
						</p>
						{latest && turn.error?.code ? (
							<Button type="button" size="sm" variant="outline" className="w-fit" disabled={waiting} onClick={onRetry}>
								{t.retry}
							</Button>
						) : null}
					</div>
				) : null}
			</div>
		</article>
	);
}

function PublicPanel({
	chat,
	onClose,
	inputRef,
}: {
	chat: PublicCobChat;
	onClose: () => void;
	inputRef: React.RefObject<HTMLTextAreaElement | null>;
}) {
	const body = useRef<HTMLDivElement>(null);
	const last = chat.turns[chat.turns.length - 1];
	const lastText = last?.text;
	const count = chat.turns.length;
	// biome-ignore lint/correctness/useExhaustiveDependencies: re-run when the text or the turn count changes
	useEffect(() => {
		const el = body.current;
		if (el && el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight;
	}, [lastText, count]);

	return (
		<div className="flex h-full min-h-0 flex-col bg-background text-foreground">
			<header className="flex items-center gap-2 border-b border-border px-3 py-2">
				<AnimatedLogo className="size-9 shrink-0" />
				<h2 className="m-0 flex-1 text-[15px] font-bold">{cob.name}</h2>
				<Button
					type="button"
					size="icon-sm"
					variant="ghost"
					aria-label={t.newChat}
					onClick={() => {
						chat.reset();
						inputRef.current?.focus();
					}}
				>
					<MessageSquarePlus aria-hidden />
				</Button>
				<Button type="button" size="icon-sm" variant="ghost" aria-label={t.close} onClick={onClose}>
					<X aria-hidden />
				</Button>
			</header>

			<div role="status" aria-live="polite" className="sr-only" data-testid="cob-live">
				{announcement(chat)}
			</div>

			<div ref={body} className="grid min-h-0 flex-1 content-start gap-4 overflow-y-auto p-3" data-testid="cob-turns">
				{chat.turns.length === 0 ? (
					<div className="grid gap-3">
						<p className="m-0 text-[14px] text-foreground-2">{t.greeting}</p>
						<ul aria-label={t.startersLabel} className="m-0 grid list-none gap-2 p-0">
							{t.starters.map((s) => (
								<li key={s}>
									<button
										type="button"
										onClick={() => void chat.send(s)}
										className="w-full rounded-md border border-border bg-card px-3 py-2 text-left text-[13px] text-foreground hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
									>
										{s}
									</button>
								</li>
							))}
						</ul>
					</div>
				) : (
					chat.turns.map((turn, i) => (
						<PublicMessage
							// biome-ignore lint/suspicious/noArrayIndexKey: turns are append-only within a chat
							key={i}
							turn={turn}
							latest={i === chat.turns.length - 1}
							onRetry={chat.retry}
							limit={chat.limit}
						/>
					))
				)}
			</div>

			<CobComposer busy={chat.busy} onSend={(m) => void chat.send(m)} onStop={chat.stop} inputRef={inputRef} />
			<div className="grid gap-1 px-3 pb-2">
				<p className="m-0 text-[12px] text-muted-foreground">{t.notSaved}</p>
				<PoweredBy tone="auth" />
			</div>
		</div>
	);
}

/**
 * "Ask COB" for people who are not signed in: a button on every sign-in-side page that opens a
 * slide-over chat (full-screen on phones). Rendered only when the engine says so (`cob_public`).
 */
export function PublicCob() {
	const { cobPublic } = useBranding();
	return cobPublic ? <PublicCobDock /> : null;
}

function PublicCobDock() {
	const [open, setOpen] = useState(false);
	const chat = usePublicCob();
	const inputRef = useRef<HTMLTextAreaElement | null>(null);
	const launcherRef = useRef<HTMLButtonElement>(null);
	return (
		<>
			<button
				ref={launcherRef}
				type="button"
				aria-label={t.launcher}
				aria-expanded={open}
				aria-haspopup="dialog"
				data-testid="cob-public-launcher"
				onClick={() => setOpen(true)}
				className="fixed right-4 bottom-4 z-40 inline-flex h-11 items-center gap-2 rounded-full border border-transparent bg-brand px-4 text-[13.5px] font-bold text-brand-foreground shadow-lg outline-none hover:brightness-110 focus-visible:ring-[3px] focus-visible:ring-ring/50"
			>
				<MessageCircle className="size-[18px]" aria-hidden />
				{t.launcher}
			</button>
			<Sheet open={open} onOpenChange={setOpen}>
				<SheetContent
					side="right"
					showCloseButton={false}
					data-testid="cob-public-sheet"
					className="w-full max-w-none gap-0 p-0 sm:max-w-[440px]"
					onOpenAutoFocus={(e) => {
						e.preventDefault();
						inputRef.current?.focus();
					}}
					onCloseAutoFocus={(e) => {
						e.preventDefault();
						launcherRef.current?.focus();
					}}
				>
					<SheetTitle className="sr-only">{t.panel}</SheetTitle>
					<SheetDescription className="sr-only">{t.description}</SheetDescription>
					<div className="min-h-0 flex-1">
						<PublicPanel chat={chat} onClose={() => setOpen(false)} inputRef={inputRef} />
					</div>
				</SheetContent>
			</Sheet>
		</>
	);
}
