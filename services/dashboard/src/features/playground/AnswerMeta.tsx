import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { CostLine } from "./CostLine";
import { answerCost, type TokenCounts } from "./cost";

/** Mockup `.msg-meta`: confidence, model, total time, tokens · cost (the engine's real figure in ₹ and $; "price unknown" when it has none). */
export function AnswerMeta({
	confidence,
	model,
	latencyMs,
	tokens,
	costUsd,
	language,
}: {
	/** Undefined while loading; null when the answer was not checked. */
	confidence: number | null | undefined;
	model: string | null;
	latencyMs: number | null;
	tokens: TokenCounts | null;
	costUsd: number | null;
	language?: string | null;
}) {
	return (
		<div data-testid="answer-meta" className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
			{language ? <span>{fill(t.chat.repliedIn, { lang: language })}</span> : null}
			{confidence === undefined ? null : confidence === null ? (
				<span title={t.meta.notCheckedHint}>{t.meta.notChecked}</span>
			) : (
				<span>{fill(t.meta.confidence, { v: confidence.toFixed(2) })}</span>
			)}
			{model ? <span className="font-mono">{model}</span> : null}
			{latencyMs != null ? <span>{fill(t.meta.seconds, { v: (latencyMs / 1000).toFixed(2) })}</span> : null}
			{tokens?.total != null ? (
				<CostLine tokens={formatNumber(tokens.total)} cost={answerCost(tokens, costUsd)} />
			) : null}
		</div>
	);
}
