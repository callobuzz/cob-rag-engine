import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useId, useMemo, useState } from "react";
import { FormAlert } from "@/components/auth/FormAlert";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Panel } from "@/features/kbs/ui";
import { usePrincipal } from "@/lib/auth/store";
import { formatDateTime } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { cn } from "@/lib/utils";
import { actorFields, type ChatMessage, chatHistoryQuery, chunksQuery, pk, portal, type Workspace } from "./api";
import { portalMessage } from "./messages";
import { ToolCallCard } from "./ToolCallCard";

const ch = t.chat;

/** The reply without its ```tool_call blocks (they are shown as cards). */
export function visibleText(content: string): string {
	return content.replace(/```tool_call[\s\S]*?```/g, "").trim();
}

/**
 * AI chat (D11): history, a message box (`POST …/chat`, `stream: false` in v1) and the proposed
 * tool calls as cards with "Apply" (`POST …/chat/execute`).
 */
export function AiChatPanel({ w, canApply }: { w: Workspace; canApply: boolean }) {
	const principal = usePrincipal();
	const qc = useQueryClient();
	const history = useQuery(chatHistoryQuery(w.id));
	// Chunk numbers for the cards (first page of chunks; ids beyond it stay as ids).
	const chunks = useQuery(chunksQuery(w.id, {}));
	const numbers = useMemo(
		() => new Map((chunks.data?.items ?? []).map((c) => [c.id, c.chunk_index + 1])),
		[chunks.data],
	);
	const [text, setText] = useState("");
	const [error, setError] = useState<string | null>(null);
	const id = useId();
	const send = useMutation({
		mutationFn: (message: string) => portal.chat(w.id, message, actorFields(principal)),
		onSuccess: () => {
			setText("");
			return qc.invalidateQueries({ queryKey: pk.chat(w.id) });
		},
		onError: (e) => setError(portalMessage(e, "chat").title),
	});
	const messages = history.data?.messages ?? [];

	return (
		<Panel title={ch.title} sub={ch.sub}>
			<div className="grid gap-4 p-4">
				{history.isPending ? (
					<CardSkeleton lines={3} />
				) : history.isError ? (
					<ErrorState error={history.error} onRetry={() => void history.refetch()} />
				) : messages.length === 0 ? (
					<p className="m-0 text-foreground-2">{ch.empty}</p>
				) : (
					<ol aria-label={ch.historyLabel} className="m-0 grid list-none gap-3 p-0">
						{messages.map((m) => (
							<Message
								key={m.id}
								m={m}
								workspaceId={w.id}
								canApply={canApply}
								numberOf={(cid) => numbers.get(cid) ?? null}
							/>
						))}
					</ol>
				)}
				{error ? <FormAlert>{error}</FormAlert> : null}
				<form
					className="grid gap-2"
					onSubmit={(e) => {
						e.preventDefault();
						const message = text.trim();
						if (!message) return;
						setError(null);
						send.mutate(message);
					}}
				>
					<label htmlFor={id} className="text-[13px] font-semibold">
						{ch.label}
					</label>
					<Textarea
						id={id}
						rows={3}
						maxLength={10000}
						placeholder={ch.placeholder}
						value={text}
						onChange={(e) => setText(e.target.value)}
						onKeyDown={(e) => {
							if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) e.currentTarget.form?.requestSubmit();
						}}
					/>
					<div className="flex justify-end">
						<Button type="submit" variant="default" disabled={send.isPending || !text.trim()}>
							<Send aria-hidden />
							{send.isPending ? ch.sending : ch.send}
						</Button>
					</div>
				</form>
			</div>
		</Panel>
	);
}

function Message({
	m,
	workspaceId,
	canApply,
	numberOf,
}: {
	m: ChatMessage;
	workspaceId: string;
	canApply: boolean;
	numberOf: (id: string) => number | null;
}) {
	const mine = m.role === "user";
	const body = mine ? m.content : visibleText(m.content);
	const tokens = typeof m.token_usage?.total_tokens === "number" ? m.token_usage.total_tokens : null;
	return (
		<li
			className={cn(
				"grid max-w-[min(100%,720px)] gap-2 rounded-lg border px-3.5 py-3",
				mine ? "justify-self-end border-transparent bg-primary-soft" : "justify-self-start border-border bg-card",
			)}
		>
			<div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
				<b className="text-foreground">{mine ? ch.you : ch.ai}</b>
				<span>{formatDateTime(m.created_at)}</span>
				{tokens != null ? <span>{fill(ch.tokens, { n: formatNumber(tokens) })}</span> : null}
			</div>
			{body ? <p className="m-0 text-[13.5px] break-words whitespace-pre-wrap">{body}</p> : null}
			{(m.tool_calls ?? []).map((call, i) => (
				<ToolCallCard
					// biome-ignore lint/suspicious/noArrayIndexKey: calls have no id; order is fixed per message
					key={i}
					workspaceId={workspaceId}
					call={call}
					canApply={canApply}
					numberOf={numberOf}
				/>
			))}
		</li>
	);
}
