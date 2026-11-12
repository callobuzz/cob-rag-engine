import { useInfiniteQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, CircleAlert, Send } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import { markAnswerSpent, streamFailure } from "@/features/playground/api";
import { CapBanner } from "@/features/playground/CapBanner";
import { AnswerTimesLine, LiveStatus } from "@/features/playground/LiveStatus";
import type { AnswerTimes } from "@/features/playground/progress";
import { setCap, useCap } from "@/features/playground/session";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { Markdown } from "@/lib/markdown";
import { fill } from "@/lib/strings/en";
import { playgroundStrings } from "@/lib/strings/playground";
import { cn } from "@/lib/utils";
import {
	type ContextChunk,
	type ConversationMessage,
	contextChunks,
	MESSAGE_MAX,
	streamReply,
	type ToolStep,
	threadQuery,
} from "./api";
import { ThinkingSteps } from "./ThinkingSteps";

const t = playgroundStrings.conversations;

/** What the engine reported for a finished reply, kept by message id for this visit. */
interface ReplyTiming {
	times: AnswerTimes;
	latencyMs: number | null;
}

interface Pending {
	user: string;
	/** `Date.now()` when the message was sent: the live clock counts from here. */
	startedAt: number;
	/** The engine's latest `stage` event (null before the first). */
	stage: string | null;
	steps: string[];
	tools: ToolStep[];
	text: string;
	status: "streaming" | "error";
	error: string | null;
}

/** Right side: the thread (oldest first, paged back), streamed replies and the composer. */
export function ConversationView({
	kb,
	tenantId,
	id,
	onGone,
}: {
	kb: string;
	tenantId: string | null | undefined;
	id: string;
	onGone: () => void;
}) {
	const qc = useQueryClient();
	const q = useInfiniteQuery(threadQuery(kb, id));
	const cap = useCap(tenantId);
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();
	const [draft, setDraft] = useState("");
	const [pending, setPending] = useState<Pending | null>(null);
	const [timingById, setTimingById] = useState<Record<string, ReplyTiming>>({});
	const [drawer, setDrawer] = useState<{ chunks: ContextChunk[]; focus: number | null } | null>(null);
	const ctl = useRef<AbortController | null>(null);
	const end = useRef<HTMLDivElement>(null);
	const ids = { input: useId(), reason: useId() };
	const messages = q.data?.pages.flatMap((p) => p.items) ?? [];
	const gone = q.isError && isApiError(q.error) && q.error.status === 404;

	// Leaving the conversation cancels its stream.
	useEffect(() => () => ctl.current?.abort(), []);
	// A conversation deleted elsewhere: refresh the list and close it.
	useEffect(() => {
		if (!gone) return;
		toast(t.notFound);
		void qc.invalidateQueries({ queryKey: qk.conversations.list(kb) });
		onGone();
	}, [gone, kb, qc, onGone]);
	// biome-ignore lint/correctness/useExhaustiveDependencies: keep the newest message in view as it streams
	useEffect(() => {
		end.current?.scrollIntoView({ block: "nearest" });
	}, [messages.length, pending?.text.length]);

	const send = async () => {
		const content = draft.trim();
		if (!content || pending?.status === "streaming" || cap) return;
		setDraft("");
		const abort = new AbortController();
		ctl.current = abort;
		let text = "";
		const p: Pending = {
			user: content,
			startedAt: Date.now(),
			stage: null,
			steps: [],
			tools: [],
			text: "",
			status: "streaming",
			error: null,
		};
		setPending(p);
		const update = (change: Partial<Pending>) => setPending((prev) => (prev ? { ...prev, ...change } : prev));
		try {
			await streamReply(
				kb,
				id,
				content,
				{
					onStage: (stage) => update({ stage }),
					onThinking: (step) => setPending((prev) => (prev ? { ...prev, steps: [...prev.steps, step] } : prev)),
					onToolCall: (tool, params) =>
						setPending((prev) =>
							prev ? { ...prev, tools: [...prev.tools, { tool, params, summary: null, durationMs: null }] } : prev,
						),
					onToolResult: (tool, summary, durationMs) =>
						setPending((prev) => {
							if (!prev) return prev;
							const tools = [...prev.tools];
							const i = tools.findLastIndex((x) => x.tool === tool && x.durationMs === null);
							if (i >= 0) tools[i] = { ...(tools[i] as ToolStep), summary, durationMs };
							else tools.push({ tool, params: {}, summary, durationMs });
							return { ...prev, tools };
						}),
					onToken: (chunk) => {
						text += chunk;
						update({ text });
					},
					onDone: async (done) => {
						if (done.messageId) {
							setTimingById((prev) => ({
								...prev,
								[done.messageId]: { times: done.times, latencyMs: done.latencyMs },
							}));
						}
						markAnswerSpent(qc);
						await Promise.all([
							qc.invalidateQueries({ queryKey: qk.conversations.thread(kb, id) }),
							qc.invalidateQueries({ queryKey: qk.conversations.list(kb) }),
						]);
						setPending(null);
					},
					onError: (detail, code) => {
						const mapped = streamFailure(detail, code);
						update({
							status: "error",
							error: mapped ? messageFor(mapped, { ragUrl, support: supportEmail }).title : detail || null,
						});
					},
				},
				abort.signal,
			);
		} catch (e) {
			if (abort.signal.aborted) return;
			const err = toApiError(e);
			if (err.code === "question_cap_reached") {
				setCap(tenantId, err);
				setPending(null);
				setDraft(content);
				return;
			}
			if (err.status === 404) {
				setPending(null);
				toast(t.notFound);
				void qc.invalidateQueries({ queryKey: qk.conversations.list(kb) });
				onGone();
				return;
			}
			update({ status: "error", error: messageFor(err, { ragUrl, support: supportEmail }).title });
		}
	};

	const streaming = pending?.status === "streaming";

	return (
		<section
			aria-label={t.threadLabel}
			className="flex min-h-[460px] min-w-0 flex-col rounded-lg border border-border bg-card min-[861px]:min-h-[560px]"
		>
			<div
				role="log"
				aria-live="polite"
				aria-busy={streaming}
				aria-label={t.threadLabel}
				data-testid="thread"
				className="flex flex-1 flex-col gap-3.5 p-4"
			>
				{q.hasPreviousPage ? (
					<Button
						size="sm"
						variant="ghost"
						className="self-center"
						disabled={q.isFetchingPreviousPage}
						onClick={() => void q.fetchPreviousPage()}
					>
						{t.loadEarlier}
					</Button>
				) : null}
				{q.isPending ? (
					<CardSkeleton lines={4} />
				) : q.isError && !gone ? (
					<ErrorState error={q.error} onRetry={() => void q.refetch()} />
				) : messages.length === 0 && !pending ? (
					<div className="m-auto grid max-w-[360px] gap-1.5 py-10 text-center">
						<b>{t.emptyThreadTitle}</b>
						<p className="m-0 text-[13px] text-foreground-2">{t.emptyThreadBody}</p>
					</div>
				) : (
					messages.map((m) => (
						<StoredMessage
							key={m.id}
							m={m}
							timing={timingById[m.id]}
							onSources={(chunks, focus) => setDrawer({ chunks, focus: focus ?? null })}
						/>
					))
				)}
				{pending ? (
					<>
						<UserBubble text={pending.user} />
						<article
							aria-label={t.assistant}
							data-testid="reply"
							data-status={pending.status}
							className="max-w-[88%] self-start rounded-[14px] rounded-bl-[4px] border border-border bg-secondary px-3.5 py-2.5 leading-[1.55]"
						>
							<ThinkingSteps steps={pending.steps} tools={pending.tools} />
							{pending.text ? (
								<div className="break-words">
									{/* Passages are only known once the reply is stored: markers stay text until then. */}
									<Markdown text={pending.text} streaming={streaming} sources={[]} />
								</div>
							) : streaming ? (
								<LiveStatus startedAt={pending.startedAt} stage={pending.stage} text="" />
							) : null}
							{streaming && pending.text ? (
								<LiveStatus
									startedAt={pending.startedAt}
									stage={pending.stage}
									text={pending.text}
									className="mt-1.5"
								/>
							) : null}
							{pending.status === "error" ? (
								<p role="alert" className="m-0 mt-1.5 flex items-start gap-1.5 text-[13px] text-destructive">
									<CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
									{fill(t.failed, { detail: pending.error ?? playgroundStrings.chat.failed })}
								</p>
							) : null}
						</article>
					</>
				) : null}
				<div ref={end} />
			</div>
			<div className="grid gap-2 border-t border-border p-3">
				{cap ? <CapBanner error={cap} /> : null}
				<form
					className="grid gap-1.5"
					onSubmit={(e) => {
						e.preventDefault();
						void send();
					}}
				>
					<div className="flex items-end gap-2">
						<label htmlFor={ids.input} className="sr-only">
							{t.messageLabel}
						</label>
						<Textarea
							id={ids.input}
							rows={2}
							value={draft}
							maxLength={MESSAGE_MAX}
							placeholder={t.placeholder}
							disabled={!!cap}
							aria-describedby={cap ? ids.reason : undefined}
							onChange={(e) => setDraft(e.target.value)}
							onKeyDown={(e) => {
								if (e.key === "Enter" && !e.shiftKey) {
									e.preventDefault();
									void send();
								}
							}}
						/>
						<Button type="submit" variant="default" disabled={!!cap || streaming || !draft.trim()}>
							<Send aria-hidden />
							{t.send}
						</Button>
					</div>
					{cap ? (
						<p id={ids.reason} className="m-0 text-xs text-muted-foreground">
							{playgroundStrings.chat.capPaused}
						</p>
					) : null}
				</form>
			</div>
			<Sheet open={!!drawer} onOpenChange={(o) => !o && setDrawer(null)}>
				<SheetContent className="w-full overflow-y-auto sm:max-w-[440px]">
					<SheetHeader>
						<SheetTitle>{t.sourcesTitle}</SheetTitle>
						<SheetDescription className="sr-only">{t.sourcesTitle}</SheetDescription>
					</SheetHeader>
					<ol className="m-0 grid list-none gap-3 px-4 pb-4">
						{(drawer?.chunks ?? []).map((c, i) => (
							<li
								key={c.chunk_id || i}
								ref={drawer?.focus === i + 1 ? focusPassage : undefined}
								tabIndex={-1}
								data-highlighted={drawer?.focus === i + 1 ? "true" : undefined}
								className={cn(
									"grid gap-1 rounded-md border border-border p-3 outline-none",
									drawer?.focus === i + 1 && "border-primary bg-primary-soft",
								)}
							>
								<span className="font-mono text-xs text-primary">
									[{i + 1}]{c.score != null ? ` · ${fill(t.score, { v: c.score.toFixed(3) })}` : ""}
								</span>
								<p className="m-0 text-[13px] break-words text-foreground-2">{c.text}</p>
							</li>
						))}
					</ol>
				</SheetContent>
			</Sheet>
		</section>
	);
}

function UserBubble({ text }: { text: string }) {
	return (
		<div className="max-w-[88%] self-end rounded-[14px] rounded-br-[4px] bg-primary px-3.5 py-2.5 leading-[1.55] break-words whitespace-pre-wrap text-primary-foreground">
			<span className="sr-only">{t.you}: </span>
			{text}
		</div>
	);
}

/** The cited passage scrolls into view and takes focus when the sources drawer opens on it. */
function focusPassage(el: HTMLLIElement | null): void {
	if (!el) return;
	el.scrollIntoView?.({ block: "nearest" });
	el.focus({ preventScroll: true });
}

function StoredMessage({
	m,
	timing,
	onSources,
}: {
	m: ConversationMessage;
	timing: ReplyTiming | undefined;
	onSources: (c: ContextChunk[], focus?: number) => void;
}) {
	if (m.role === "user") return <UserBubble text={m.content} />;
	const chunks = contextChunks(m);
	return (
		<article
			aria-label={t.assistant}
			className="max-w-[88%] self-start rounded-[14px] rounded-bl-[4px] border border-border bg-secondary px-3.5 py-2.5 leading-[1.55]"
		>
			<div className="break-words">
				<Markdown
					text={m.content}
					sources={chunks.map((_, i) => i + 1)}
					onCite={chunks.length ? (n) => onSources(chunks, n) : undefined}
				/>
			</div>
			<div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
				<span>{formatDateTime(m.created_at)}</span>
				{m.token_count ? <span>{fill(t.tokenCount, { n: formatNumber(m.token_count) })}</span> : null}
				{chunks.length ? (
					<Button size="sm" variant="ghost" className="h-6 px-1.5" onClick={() => onSources(chunks)}>
						<BookOpen aria-hidden />
						{fill(t.sources, { n: chunks.length })}
					</Button>
				) : null}
			</div>
			{timing ? <AnswerTimesLine totalMs={timing.latencyMs} times={timing.times} /> : null}
		</article>
	);
}
