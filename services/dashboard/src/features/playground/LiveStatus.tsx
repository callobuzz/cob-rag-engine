import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { cn } from "@/lib/utils";
import { type AnswerTimes, estimateTokens, formatDuration, liveStageLabel, stagePieces, useElapsed } from "./progress";

/**
 * The line under a streaming answer: what the engine is doing now (its latest `stage`), a clock
 * that started when the question was sent, and a running token estimate once words arrive. The
 * clock is hidden from screen readers (it ticks ten times a second); the stage label is not.
 */
export function LiveStatus({
	startedAt,
	stage,
	text,
	className,
}: {
	/** `Date.now()` when the question was sent. */
	startedAt: number;
	stage: string | null | undefined;
	/** The answer so far, for the token estimate. */
	text: string;
	className?: string;
}) {
	const elapsed = useElapsed(startedAt, true);
	const tokens = estimateTokens(text);
	return (
		<p
			data-testid="live-status"
			data-stage={stage ?? ""}
			className={cn("m-0 flex flex-wrap items-center gap-x-2.5 gap-y-0.5 text-xs text-muted-foreground", className)}
		>
			<span data-testid="live-stage">{liveStageLabel(stage)}</span>
			<span aria-hidden data-testid="live-clock" className="font-mono tabular-nums">
				{fill(t.live.clock, { v: (elapsed / 1000).toFixed(1) })}
			</span>
			{tokens > 0 ? <span data-testid="live-tokens">{fill(t.live.tokensSoFar, { n: tokens })}</span> : null}
		</p>
	);
}

/**
 * Under a finished answer: total time, the time of each stage that ran (a stage that was off is
 * not listed), and — only when the model reported it reasoned — thinking time and reasoning tokens
 * on their own, since that time is already inside "Writing".
 */
export function AnswerTimesLine({ totalMs, times }: { totalMs: number | null; times: AnswerTimes }) {
	const pieces = stagePieces(times.stages);
	if (totalMs == null && pieces.length === 0 && times.thinkingMs == null) return null;
	return (
		<ul
			data-testid="answer-times"
			aria-label={t.times.label}
			className="m-0 mt-1 flex list-none flex-wrap gap-x-3 gap-y-1 p-0 text-xs text-muted-foreground"
		>
			{totalMs != null ? <li data-testid="time-total">{fill(t.times.total, { v: formatDuration(totalMs) })}</li> : null}
			{pieces.map((p) => (
				<li key={p.key} data-testid={`time-${p.key}`}>
					{p.text}
				</li>
			))}
			{times.thinkingMs != null ? (
				<li data-testid="time-thinking" title={t.times.thinkingHint}>
					{fill(t.times.thinking, { v: formatDuration(times.thinkingMs) })}
					{times.reasoningTokens != null ? ` · ${fill(t.times.reasoningTokens, { n: times.reasoningTokens })}` : ""}
				</li>
			) : null}
		</ul>
	);
}
