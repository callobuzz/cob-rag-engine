import { useEffect, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Markdown } from "@/lib/markdown/Markdown";
import { cobStrings as t } from "@/lib/strings/cob";
import { ActionCard } from "./actions/ActionCard"; // COB5
import { CobFeedback } from "./CobFeedback";
import { CobLinkButton } from "./CobLinkButton";
import { CobSearchResults } from "./CobSearchResults";
import { CobSources } from "./CobSources";
import { CobStageLine } from "./CobStageLine";
import type { CobActionCardBlock, CobBlock, CobLinkBlock, CobSearchResultsBlock, CobTurnView } from "./types";

export interface CobMessageProps {
	turn: CobTurnView;
	latest: boolean;
	busy: boolean;
	onSend: (message: string) => void;
	onRetry: () => void;
	onRated: (messageId: string, rating: 1 | -1) => void;
}

/** First run of the engine: the docs are being read; try again by itself after this long. */
export const SYNC_RETRY_MS = 30_000;

/** One block of a turn, by kind. Unknown kinds render nothing. */
function Block({ block, onAskAgain }: { block: CobBlock; onAskAgain: () => void }) {
	switch (block.kind) {
		case "cob_link":
			return <CobLinkButton block={block as CobLinkBlock} />;
		case "cob_search_results":
			return <CobSearchResults block={block as CobSearchResultsBlock} />;
		// COB5
		case "cob_action_card":
			return <ActionCard block={block as unknown as CobActionCardBlock} onAskAgain={onAskAgain} />;
		default:
			return null;
	}
}

/** One question and what COB did with it: the answer (Markdown), its sources, links, results and thumbs. */
export function CobMessage({ turn, latest, busy, onSend, onRetry, onRated }: CobMessageProps) {
	const sourcesRef = useRef<HTMLDivElement>(null);
	const streaming = turn.status === "streaming";
	const syncing = turn.status === "error" && turn.error?.code === "cob_docs_syncing";

	useEffect(() => {
		if (!syncing || !latest) return;
		const id = setTimeout(onRetry, SYNC_RETRY_MS);
		return () => clearTimeout(id);
	}, [syncing, latest, onRetry]);

	const focusSource = (n: number) => {
		sourcesRef.current?.querySelector<HTMLElement>(`[data-source="${n}"]`)?.focus();
	};

	return (
		<article className="grid gap-3" data-testid="cob-turn" aria-label={turn.question}>
			{
				turn.question ? (
					<p className="m-0 ml-auto max-w-[85%] rounded-lg bg-secondary px-3 py-2 text-[13.5px] break-words whitespace-pre-wrap">
						<span className="sr-only">{t.you}: </span>
						{turn.question}
					</p>
				) : null /* COB5: the note COB adds after a card is confirmed has no question of its own */
			}

			<div className="grid min-w-0 gap-2.5 text-[13.5px]">
				<span className="sr-only">{t.cob}: </span>
				{streaming && !turn.text ? <CobStageLine stage={turn.stage} /> : null}
				{turn.text ? (
					<Markdown text={turn.text} streaming={streaming} onCite={turn.sources.length ? focusSource : undefined} />
				) : null}

				{turn.sources.length ? (
					<div ref={sourcesRef}>
						<CobSources sources={turn.sources} />
					</div>
				) : null}

				{turn.blocks.map((b, i) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: blocks are append-only, in stream order
					<Block key={i} block={b} onAskAgain={() => onSend(turn.question)} />
				))}

				{turn.status === "error" ? (
					<div role="alert" className="grid gap-2 rounded-md border border-border bg-card p-3" data-testid="cob-error">
						<p className="m-0 text-[13px]">{syncing ? t.errors.syncing : turn.error?.detail || t.errors.generic}</p>
						{syncing && latest ? (
							<p className="m-0 text-[12px] text-muted-foreground">{t.errors.syncingRetry}</p>
						) : null}
						{latest ? (
							<Button type="button" size="sm" variant="outline" className="w-fit" onClick={onRetry}>
								{t.errors.retry}
							</Button>
						) : null}
					</div>
				) : null}

				{turn.status === "done" && latest && turn.quickReplies.length ? (
					<ul aria-label={t.quickReplies} className="m-0 flex list-none flex-wrap gap-1.5 p-0">
						{turn.quickReplies.map((q) => (
							<li key={q.label}>
								<button
									type="button"
									disabled={busy}
									onClick={() => onSend(q.message)}
									className="rounded-full border border-border bg-card px-3 py-1 text-[12.5px] hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none disabled:opacity-50"
								>
									{q.label}
								</button>
							</li>
						))}
					</ul>
				) : null}

				{turn.status === "done" && turn.messageId && turn.mode !== "search" ? (
					<CobFeedback messageId={turn.messageId} rating={turn.rating} onRated={onRated} />
				) : null}
			</div>
		</article>
	);
}
