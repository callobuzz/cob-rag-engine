import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Send, Square } from "lucide-react";
import { useId, useState } from "react";
import { FormAlert } from "@/components/auth/FormAlert";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { modelCatalogQuery } from "@/features/kbs/api";
import { markAnswerSpent } from "@/features/playground/api";
import { ChatPane } from "@/features/playground/ChatPane";
import { type BotMsg, stop, useBusy, useCap, useKbSession } from "@/features/playground/session";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { playgroundStrings as p } from "@/lib/strings/playground";
import type { CorpusDetail } from "../../kbs/api";
import { ReplyExtras } from "../replies/ReplyExtras";
import { PreviewUiCard } from "./PreviewUiCard";
import { previewAsk, previewKey, type Simulate } from "./stream";

/** What follows a finished preview answer: suggested replies, the forms and cards it offered, the next step. */
function Extras({
	msg,
	latest,
	onSend,
	disabled,
}: {
	msg: BotMsg;
	latest: boolean;
	onSend: (text: string) => void;
	disabled: boolean;
}) {
	if (msg.status !== "done") return null;
	const replies = msg.quickReplies ?? [];
	const ui = msg.ui ?? [];
	const cta = msg.done?.cta;
	if (!replies.length && !ui.length && !cta) return null;
	return (
		<div className="mt-2 grid gap-2" data-testid="preview-extras">
			{ui.map((b, i) => (
				// biome-ignore lint/suspicious/noArrayIndexKey: at most one block per turn, in stream order
				<PreviewUiCard key={i} block={b} />
			))}
			<ReplyExtras replies={replies} cta={cta} latest={latest} disabled={disabled} onSend={onSend} />
		</div>
	);
}

/** The preview chat: messages, composer, and the suggested replies the assistant offers. */
export function PreviewChat({
	kb,
	debug,
	simulate,
	onPick,
}: {
	kb: CorpusDetail;
	debug: boolean;
	simulate: Simulate;
	onPick: (messageId: string, index: number) => void;
}) {
	const qc = useQueryClient();
	const key = previewKey(kb.id);
	const session = useKbSession(key);
	const streaming = useBusy(key);
	/** True from the click, before the stream registers: the conversation POST runs first. */
	const [sending, setSending] = useState(false);
	const busy = streaming || sending;
	const cap = useCap(kb.tenant_id);
	const catalog = useQuery(modelCatalogQuery).data?.models;
	const [question, setQuestion] = useState("");
	const ids = { input: useId(), reason: useId() };
	const lastBot = [...session.messages].reverse().find((m): m is BotMsg => m.role === "bot");
	const denied = lastBot?.failure?.code === "key_permission_denied";

	const send = async (text: string) => {
		const q = text.trim();
		if (!q || busy || cap || denied) return;
		setQuestion("");
		setSending(true);
		try {
			await previewAsk({ kb: kb.id, tenantId: kb.tenant_id, question: q, debug, simulate }, () => markAnswerSpent(qc));
		} finally {
			setSending(false);
		}
	};

	return (
		<section
			aria-label={t.preview.title}
			className="flex min-h-[420px] min-w-0 flex-col rounded-lg border border-border bg-card min-[861px]:min-h-[560px]"
		>
			<ChatPane
				session={session}
				language={null}
				catalog={catalog}
				label={t.preview.log}
				hideFeedback
				onPick={onPick}
				onFeedback={() => {}}
				empty={
					<div className="m-auto grid max-w-[360px] justify-items-center gap-2 py-10 text-center">
						<h2 className="text-[17px] font-bold">{t.preview.emptyTitle}</h2>
						<p className="m-0 text-[13px] text-foreground-2">{t.preview.emptyBody}</p>
					</div>
				}
				extras={(m) => (
					<Extras
						msg={m}
						latest={m.id === lastBot?.id}
						disabled={busy || !!cap || denied}
						onSend={(x) => void send(x)}
					/>
				)}
			/>
			{denied ? (
				<div className="px-3 pb-2">
					<FormAlert tone="warn">{t.preview.noWrite}</FormAlert>
				</div>
			) : null}
			<form
				className="grid gap-1.5 border-t border-border p-3"
				onSubmit={(e) => {
					e.preventDefault();
					void send(question);
				}}
			>
				<div className="flex gap-2">
					<label htmlFor={ids.input} className="sr-only">
						{t.preview.message}
					</label>
					<Input
						id={ids.input}
						value={question}
						placeholder={t.preview.placeholder}
						disabled={!!cap || denied}
						aria-describedby={cap ? ids.reason : undefined}
						onChange={(e) => setQuestion(e.target.value)}
					/>
					{busy ? (
						<Button type="button" variant="outline" onClick={() => stop(key)}>
							<Square aria-hidden />
							{t.preview.stop}
						</Button>
					) : (
						<Button type="submit" variant="default" disabled={!!cap || denied || !question.trim()}>
							<Send aria-hidden />
							{t.preview.send}
						</Button>
					)}
				</div>
				{cap ? (
					<p id={ids.reason} className="m-0 text-xs text-muted-foreground">
						{p.chat.capPaused}
					</p>
				) : null}
			</form>
		</section>
	);
}
