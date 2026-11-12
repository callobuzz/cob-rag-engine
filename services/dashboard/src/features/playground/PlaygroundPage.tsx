import { useQuery, useQueryClient } from "@tanstack/react-query";
import { getRouteApi, Link } from "@tanstack/react-router";
import { Columns2, MessagesSquare, Send, SlidersHorizontal, Square, Trash2 } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { toast } from "sonner";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button, buttonVariants } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ReplyExtras } from "@/features/assistant/replies/ReplyExtras";
import { corpusQuery, modelCatalogQuery } from "@/features/kbs/api";
import { messageFor } from "@/lib/api/error-messages";
import { type Corpus, infoQuery } from "@/lib/api/queries";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { cn } from "@/lib/utils";
import { EMPTY_OVERRIDE, type LlmOverride, markAnswerSpent } from "./api";
import { ChatPane } from "./ChatPane";
import { ComparePane } from "./ComparePane";
import { langFor } from "./Message";
import { PipelineStrip } from "./PipelineStrip";
import { SessionTotals } from "./SessionTotals";
import { SourcesPanel, sourceDomId } from "./SourcesPanel";
import {
	ask,
	citationsToSources,
	clearSession,
	compare,
	setActive,
	stop,
	updateBot,
	useBusy,
	useCap,
	useKbSession,
	withBreakdowns,
} from "./session";
import { generationMs } from "./stages";
import { TrySettingsPanel } from "./TrySettingsPanel";
import { buildTry, EMPTY_TRY, type TryState } from "./try-settings";

const route = getRouteApi("/_app/playground");
const QUERY_MAX_FALLBACK = 2000;

/** `/playground?kb=&q=` (mockup `[data-view=playground]`). */
export function PlaygroundPage() {
	return (
		<KbScopedPage title={t.page.title} emptyBody={t.common.noKbBody}>
			{(kb) => <Playground key={kb.id} kb={kb} />}
		</KbScopedPage>
	);
}

function Playground({ kb }: { kb: Corpus }) {
	const { q } = route.useSearch();
	const qc = useQueryClient();
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();
	const session = useKbSession(kb.id);
	const busy = useBusy(kb.id);
	const cap = useCap(kb.tenant_id);
	const catalog = useQuery(modelCatalogQuery).data?.models;
	const detail = useQuery(corpusQuery(kb.id));
	const info = useQuery(infoQuery);
	const language = langFor(detail.data?.prompt_config?.response_language);
	const maxQuery = info.data?.limits.max_query_chars ?? QUERY_MAX_FALLBACK;

	const [tryOpen, setTryOpen] = useState(false);
	const [compareOn, setCompareOn] = useState(false);
	const [tryState, setTryState] = useState<TryState>(EMPTY_TRY);
	const [override, setOverride] = useState<LlmOverride>(EMPTY_OVERRIDE);
	const [question, setQuestion] = useState(q ?? "");
	const [inlineError, setInlineError] = useState<string | null>(null);
	const [highlight, setHighlight] = useState<{ id: string; index: number } | null>(null);
	const built = useMemo(() => buildTry(tryState), [tryState]);
	const ids = { try: useId(), input: useId(), reason: useId() };

	const onSpent = () => markAnswerSpent(qc);
	// A tapped suggestion chip is a normal question (metered like one) and leaves the draft alone.
	const submit = async (chip?: string) => {
		const text = (chip ?? question).trim();
		if (!text || busy || cap) return;
		if (text.length > maxQuery) {
			setInlineError(fill(t.chat.tooLong, { max: maxQuery.toLocaleString("en-IN") }));
			return;
		}
		if (!built.ok) {
			setTryOpen(true);
			setInlineError(t.try.invalid);
			return;
		}
		setInlineError(null);
		const req = {
			kb: kb.id,
			tenantId: kb.tenant_id,
			question: text,
			options: built.options,
			systemPrompt: built.systemPrompt,
			llm: override,
		};
		if (!compareOn && chip === undefined) setQuestion("");
		const outcome = compareOn ? await compare(req, onSpent) : await ask(req, onSpent);
		if (outcome.kind === "invalid") {
			if (chip === undefined) setQuestion(text);
			const e = outcome.error;
			setInlineError(
				e.fieldErrors.some((f) => f.path === "query")
					? fill(t.chat.tooLong, { max: maxQuery.toLocaleString("en-IN") })
					: messageFor(e, { ragUrl, support: supportEmail }).title,
			);
		}
	};

	const lastBotId = session.messages.findLast((m) => m.role === "bot")?.id;

	const pick = (messageId: string, index: number) => {
		setActive(kb.id, messageId);
		setHighlight({ id: messageId, index });
		requestAnimationFrame(() => document.getElementById(sourceDomId(messageId, index))?.focus());
	};

	// Right stack: the active answer, or Try settings' side of the latest compare.
	const latestCompare = session.compares.at(-1) ?? null;
	const activeBot = session.messages.find((m) => m.id === session.activeId && m.role === "bot");
	const focus = (() => {
		if (compareOn) {
			const b = latestCompare?.b;
			if (!b) return null;
			return {
				id: `${latestCompare.id}-b`,
				sources: b.response ? withBreakdowns(citationsToSources(b.response), b.retrieve) : [],
				scoresState: b.retrieveState,
				timings: b.retrieve?.stage_timings_ms,
				timingsState: b.retrieveState,
				totalMs: b.response?.latency_ms ?? null,
				writeMs: generationMs(b.response?.latency_ms, b.retrieve?.latency_ms),
				sub: t.compare.b,
			};
		}
		if (activeBot?.role !== "bot") return null;
		return {
			id: activeBot.id,
			sources: activeBot.sources,
			scoresState: activeBot.scoresState,
			timings: activeBot.times.steps ?? undefined,
			timingsState: activeBot.timingsState,
			totalMs: activeBot.latencyMs,
			// The stream reports the writing time itself; a non-streamed answer's is what is left
			// of its latency after the search.
			writeMs: activeBot.times.stages?.write ?? generationMs(activeBot.latencyMs, activeBot.times.retrievalMs),
			sub: undefined,
		};
	})();

	return (
		<>
			<PageHeader
				title={t.page.title}
				description={fill(t.page.lead, { kb: kb.name })}
				actions={
					<>
						<Button
							variant={tryOpen ? "secondary" : "outline"}
							aria-expanded={tryOpen}
							aria-controls={ids.try}
							onClick={() => setTryOpen((v) => !v)}
						>
							<SlidersHorizontal aria-hidden />
							{t.page.trySettings}
						</Button>
						<Button
							variant={compareOn ? "default" : "outline"}
							aria-pressed={compareOn}
							onClick={() => setCompareOn((v) => !v)}
						>
							<Columns2 aria-hidden />
							{t.page.compare}
						</Button>
						<SessionTotals session={session} catalog={catalog} />
						<Link to="/conversations" search={{ kb: kb.id }} className={buttonVariants({ variant: "outline" })}>
							<MessagesSquare aria-hidden />
							{t.page.conversations}
						</Link>
						<Button
							variant="ghost"
							onClick={() => {
								clearSession(kb.id);
								setHighlight(null);
								toast(t.page.clearDone);
							}}
						>
							<Trash2 aria-hidden />
							{t.page.clear}
						</Button>
					</>
				}
			/>
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
				<section
					aria-label={t.page.title}
					className="flex min-h-[460px] min-w-0 flex-col rounded-lg border border-border bg-card min-[861px]:min-h-[620px]"
				>
					{tryOpen ? (
						<TrySettingsPanel
							id={ids.try}
							kbId={kb.id}
							value={tryState}
							onChange={setTryState}
							errors={built.ok ? {} : built.errors}
							override={override}
							onOverride={setOverride}
						/>
					) : null}
					{compareOn ? (
						<ComparePane run={latestCompare} catalog={catalog} language={language} />
					) : (
						<ChatPane
							session={session}
							language={language}
							catalog={catalog}
							onPick={pick}
							onFeedback={(id, v) => updateBot(kb.id, id, { feedback: v })}
							extras={(m) =>
								m.status === "done" ? (
									<ReplyExtras
										replies={m.quickReplies ?? []}
										cta={m.done?.cta}
										latest={m.id === lastBotId}
										disabled={busy || !!cap}
										onSend={(x) => void submit(x)}
									/>
								) : null
							}
						/>
					)}
					<form
						className="grid gap-1.5 border-t border-border p-3"
						onSubmit={(e) => {
							e.preventDefault();
							void submit();
						}}
					>
						<div className="flex gap-2">
							<label htmlFor={ids.input} className="sr-only">
								{t.chat.question}
							</label>
							<Input
								id={ids.input}
								value={question}
								placeholder={t.chat.placeholder}
								disabled={!!cap}
								aria-invalid={!!inlineError || undefined}
								aria-describedby={cap || inlineError ? ids.reason : undefined}
								onChange={(e) => {
									setQuestion(e.target.value);
									if (inlineError) setInlineError(null);
								}}
							/>
							{busy ? (
								<Button type="button" variant="outline" onClick={() => stop(kb.id)}>
									<Square aria-hidden />
									{t.chat.stop}
								</Button>
							) : (
								<Button type="submit" variant="default" disabled={!!cap || !question.trim()}>
									<Send aria-hidden />
									{compareOn ? t.chat.askCompare : t.chat.ask}
								</Button>
							)}
						</div>
						{cap || inlineError ? (
							<p
								id={ids.reason}
								role={inlineError ? "alert" : undefined}
								className={cn("m-0 text-xs", inlineError ? "text-destructive" : "text-muted-foreground")}
							>
								{inlineError ?? t.chat.capPaused}
							</p>
						) : null}
					</form>
				</section>
				<div className="grid min-w-0 gap-4">
					<SourcesPanel
						sources={focus?.sources ?? []}
						scoresState={focus?.scoresState ?? "pending"}
						highlight={highlight && highlight.id === focus?.id ? highlight.index : null}
						idPrefix={focus?.id ?? "none"}
						sub={focus?.sub}
					/>
					<PipelineStrip
						timings={focus?.timings}
						writeMs={focus?.writeMs ?? null}
						totalMs={focus?.totalMs ?? null}
						state={focus ? focus.timingsState : "none"}
					/>
				</div>
			</div>
		</>
	);
}
