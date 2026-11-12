import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Hand } from "lucide-react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDateTime } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { cn } from "@/lib/utils";
import { type ThreadMessage, threadQuery, useSetPaused } from "./api";
import { ReplyBox } from "./ReplyBox";

const BUBBLE: Record<ThreadMessage["role"], string> = {
	user: "mr-auto bg-muted",
	assistant: "ml-auto bg-brand-soft",
	staff: "ml-auto bg-primary/10 border border-primary/30",
};

/**
 * One chat (TV6): the customer's messages, the assistant's and yours, labelled; a banner when the
 * assistant is staying quiet (with "Let the assistant answer again"); and the reply box. Opening a
 * chat marks it read.
 */
export function ChatView({ kb, threadId, onBack }: { kb: string; threadId: string; onBack?: () => void }) {
	const q = useQuery(threadQuery(kb, threadId));
	const paused = useSetPaused(kb, threadId);
	if (q.isPending) return <CardSkeleton lines={4} />;
	if (q.isError) return <ErrorState error={q.error} title={t.chat.loadError} onRetry={() => void q.refetch()} />;
	const chat = q.data;
	const until = chat.bot_paused_until;
	const isPaused = !!until;
	const setPaused = (value: boolean) =>
		paused.mutate(value, {
			onSuccess: () => toast.success(value ? t.chat.pausedNow : t.chat.resumed),
			onError: (e) => toast.error(messageFor(toApiError(e)).title),
		});

	return (
		<div className="grid min-w-0 gap-3" data-testid="wa-chat">
			<div className="flex items-center gap-2">
				{onBack ? (
					<Button variant="ghost" size="sm" onClick={onBack}>
						<ArrowLeft aria-hidden />
						{t.chat.back}
					</Button>
				) : null}
				<div className="min-w-0">
					<h2 className="m-0 truncate text-[15px] font-bold">{chat.profile_name ?? chat.name_masked}</h2>
					<small className="text-muted-foreground">{fill(t.chat.number, { number: chat.contact })}</small>
				</div>
			</div>
			{isPaused ? (
				<div
					className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-border bg-muted p-3 text-[13px]"
					data-testid="wa-paused"
				>
					<span className="flex items-center gap-2">
						<Hand className="size-4" aria-hidden />
						{fill(t.chat.pausedBanner, { until: until ? ` (${formatDateTime(until)})` : "" })}
					</span>
					<Button
						size="sm"
						variant="outline"
						onClick={() => setPaused(false)}
						aria-disabled={paused.isPending || undefined}
					>
						{t.chat.resume}
					</Button>
				</div>
			) : (
				<div>
					<Button
						size="sm"
						variant="ghost"
						onClick={() => setPaused(true)}
						aria-disabled={paused.isPending || undefined}
					>
						{t.chat.pauseNow}
					</Button>
				</div>
			)}
			<ol className="m-0 grid max-h-[420px] list-none gap-2 overflow-y-auto p-0" aria-label={t.chats.title}>
				{chat.messages.length === 0 ? <li className="text-muted-foreground">{t.chat.empty}</li> : null}
				{chat.messages.map((m) => (
					<li
						key={m.id}
						className={cn("max-w-[85%] rounded-lg px-3 py-2 text-[13.5px]", BUBBLE[m.role])}
						data-role={m.role}
					>
						<span className="block text-[11.5px] font-semibold text-foreground-2">
							{t.chat.who[m.role] ?? m.role} · {formatDateTime(m.created_at)}
						</span>
						<span className="whitespace-pre-wrap break-words">{m.content}</span>
					</li>
				))}
			</ol>
			<ReplyBox kb={kb} threadId={threadId} windowOpen={chat.window_open} />
		</div>
	);
}
