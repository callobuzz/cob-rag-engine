import { CircleAlert, Square } from "lucide-react";
import type { ReactNode } from "react";
import { messageFor } from "@/lib/api/error-messages";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { Markdown } from "@/lib/markdown";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { cn } from "@/lib/utils";
import { AnswerMeta } from "./AnswerMeta";
import { CapBanner } from "./CapBanner";
import { Citations } from "./Citations";
import { costUsd, type ModelEntry } from "./cost";
import { FeedbackButtons } from "./FeedbackButtons";
import { AnswerTimesLine, LiveStatus } from "./LiveStatus";
import type { BotMsg, ChatMsg } from "./session";

/** The knowledge base's reply language (`prompt_config.response_language`) → BCP 47 `lang`. */
const LANG_CODES: Record<string, string> = {
	english: "en",
	hindi: "hi",
	marathi: "mr",
	tamil: "ta",
	telugu: "te",
	bengali: "bn",
	gujarati: "gu",
	kannada: "kn",
	malayalam: "ml",
	punjabi: "pa",
	odia: "or",
	urdu: "ur",
};

const LANG_LABELS: Record<string, string> = {
	en: "English",
	hi: "Hindi",
	"hi-Latn": "Hinglish",
	mr: "Marathi",
	ta: "Tamil",
	te: "Telugu",
	bn: "Bengali",
	gu: "Gujarati",
	kn: "Kannada",
	ml: "Malayalam",
	pa: "Punjabi",
};

export function langFor(responseLanguage: unknown): { code: string; label: string } | null {
	if (typeof responseLanguage !== "string" || !responseLanguage.trim()) return null;
	const label = responseLanguage.trim();
	const code = LANG_CODES[label.toLowerCase()] ?? (/^[a-z]{2,3}(-[A-Za-z]{2,4})?$/.test(label) ? label : null);
	return code ? { code, label } : null;
}

/** User bubble (right, primary) or bot bubble (streamed text + caret, citations, meta, feedback). */
export function Message({
	msg,
	language,
	catalog,
	active,
	onPick,
	onFeedback,
	hideFeedback,
	extras,
}: {
	msg: ChatMsg;
	language: { code: string; label: string } | null;
	catalog: readonly ModelEntry[] | undefined;
	active: boolean;
	onPick: (index: number) => void;
	onFeedback: (v: "up" | "down") => void;
	/** The preview console writes no feedback rows for its test chats. */
	hideFeedback?: boolean;
	/** More under a finished answer (quick replies, forms, the next step). */
	extras?: (msg: BotMsg) => ReactNode;
}) {
	if (msg.role === "user") {
		return (
			<div className="max-w-[88%] self-end rounded-[14px] rounded-br-[4px] bg-primary px-3.5 py-2.5 leading-[1.55] break-words whitespace-pre-wrap text-primary-foreground">
				<span className="sr-only">{t.chat.you}: </span>
				{msg.text}
			</div>
		);
	}
	if (msg.role === "cap") return <CapBanner error={msg.error} />;
	return (
		<Bot
			msg={msg}
			language={language}
			catalog={catalog}
			active={active}
			onPick={onPick}
			onFeedback={onFeedback}
			hideFeedback={hideFeedback}
			extras={extras}
		/>
	);
}

function Bot({
	msg,
	language: kbLanguage,
	catalog,
	active,
	onPick,
	onFeedback,
	hideFeedback,
	extras,
}: {
	msg: BotMsg;
	language: { code: string; label: string } | null;
	catalog: readonly ModelEntry[] | undefined;
	active: boolean;
	onPick: (index: number) => void;
	onFeedback: (v: "up" | "down") => void;
	hideFeedback?: boolean;
	extras?: (msg: BotMsg) => ReactNode;
}) {
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();
	// The assistant's reply policy decides the language; it overrides the KB's `response_language`.
	const language = msg.metaLanguage
		? { code: msg.metaLanguage, label: LANG_LABELS[msg.metaLanguage] ?? msg.metaLanguage }
		: kbLanguage;
	const streaming = msg.status === "streaming";
	const waiting = streaming && !msg.text;
	return (
		<article
			data-testid="bot-answer"
			data-status={msg.status}
			aria-label={t.chat.assistant}
			className={cn(
				"max-w-[88%] self-start rounded-[14px] rounded-bl-[4px] border border-border bg-secondary px-3.5 py-2.5 leading-[1.55]",
				active && "border-[color-mix(in_srgb,var(--primary)_40%,var(--border))]",
			)}
		>
			{waiting ? (
				<div className="flex items-center gap-1">
					<LiveStatus startedAt={msg.startedAt} stage={msg.stage} text="" />
					<span aria-hidden className="inline-block h-3.5 w-[7px] animate-blink bg-primary motion-reduce:hidden" />
				</div>
			) : msg.text ? (
				<div data-testid="answer-text" lang={language?.code} className="break-words">
					{/* Markdown, safely: a citation chip highlights that passage in Sources. The caret is CSS
					    (`.md-streaming`), riding on the last block instead of dropping to its own line. */}
					<Markdown text={msg.text} streaming={streaming} sources={msg.sources.map((s) => s.index)} onCite={onPick} />
				</div>
			) : null}
			{streaming && msg.text ? (
				<LiveStatus startedAt={msg.startedAt} stage={msg.stage} text={msg.text} className="mt-1.5" />
			) : null}
			{msg.status === "stopped" ? (
				<p className="m-0 mt-1.5 flex items-center gap-1.5 text-xs text-muted-foreground">
					<Square className="size-3" aria-hidden />
					{t.chat.stopped}
				</p>
			) : null}
			{msg.status === "error" ? (
				<p role="alert" className="m-0 mt-1.5 flex items-start gap-1.5 text-[13px] text-destructive">
					<CircleAlert className="mt-0.5 size-3.5 shrink-0" aria-hidden />
					{msg.failure ? messageFor(msg.failure, { ragUrl, support: supportEmail }).title : msg.error || t.chat.failed}
				</p>
			) : null}
			{msg.note ? (
				<p className="m-0 mt-1.5 text-xs text-muted-foreground">
					{msg.note === "streamOff" ? t.chat.streamOff : t.chat.notStreamedCheck}
				</p>
			) : null}
			<Citations sources={msg.sources} onPick={onPick} />
			{msg.status === "done" || (msg.status !== "streaming" && msg.latencyMs != null) ? (
				<AnswerMeta
					confidence={msg.confidence}
					model={msg.model}
					latencyMs={msg.latencyMs}
					tokens={msg.tokens}
					costUsd={costUsd(catalog, msg.model, msg.tokens)}
					language={language?.label ?? null}
				/>
			) : null}
			{msg.status === "done" ? <AnswerTimesLine totalMs={msg.latencyMs} times={msg.times} /> : null}
			{msg.status === "done" && msg.queryId && !hideFeedback ? (
				<div className="mt-1.5">
					<FeedbackButtons queryId={msg.queryId} sent={msg.feedback} onSent={onFeedback} />
				</div>
			) : null}
			{extras ? extras(msg) : null}
		</article>
	);
}
