import { MessageSquare } from "lucide-react";
import { type ReactNode, useEffect, useRef } from "react";
import { playgroundStrings as t } from "@/lib/strings/playground";
import type { ModelEntry } from "./cost";
import { Message } from "./Message";
import type { BotMsg, KbSession } from "./session";

/**
 * Mockup `#chatView`: the message list is a `log` region. While an answer streams the region is
 * `aria-busy`, so screen readers announce the finished answer once instead of every token.
 */
export function ChatPane({
	session,
	language,
	catalog,
	onPick,
	onFeedback,
	hideFeedback,
	extras,
	empty,
	label,
}: {
	session: KbSession;
	language: { code: string; label: string } | null;
	catalog: readonly ModelEntry[] | undefined;
	onPick: (messageId: string, index: number) => void;
	onFeedback: (messageId: string, v: "up" | "down") => void;
	hideFeedback?: boolean;
	extras?: (msg: BotMsg) => ReactNode;
	/** The empty state; the playground's by default. */
	empty?: ReactNode;
	/** The log region's accessible name. */
	label?: string;
}) {
	const end = useRef<HTMLDivElement>(null);
	const streaming = session.messages.some((m) => m.role === "bot" && m.status === "streaming");
	const last = session.messages.at(-1);
	const lastLength = last?.role === "bot" ? last.text.length : 0;
	// Keep the newest message in view as it streams (a DOM effect, not data fetching).
	// biome-ignore lint/correctness/useExhaustiveDependencies: scroll whenever a message is added or grows
	useEffect(() => {
		end.current?.scrollIntoView({ block: "nearest" });
	}, [session.messages.length, lastLength]);

	return (
		<div
			role="log"
			aria-live="polite"
			aria-busy={streaming}
			aria-label={label ?? t.chat.logLabel}
			data-testid="chat-log"
			className="flex flex-1 flex-col gap-3.5 p-4"
		>
			{session.messages.length === 0
				? (empty ?? (
						<div className="m-auto grid max-w-[360px] justify-items-center gap-2 py-10 text-center">
							<MessageSquare className="size-8 text-primary" aria-hidden />
							<h2 className="text-[17px] font-bold">{t.chat.emptyTitle}</h2>
							<p className="m-0 text-[13px] text-foreground-2">{t.chat.emptyBody}</p>
						</div>
					))
				: session.messages.map((m) => (
						<Message
							key={m.id}
							msg={m}
							language={language}
							catalog={catalog}
							active={m.id === session.activeId}
							onPick={(i) => onPick(m.id, i)}
							onFeedback={(v) => onFeedback(m.id, v)}
							hideFeedback={hideFeedback}
							extras={extras}
						/>
					))}
			<div ref={end} />
		</div>
	);
}
