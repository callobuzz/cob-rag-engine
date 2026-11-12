import { History, MessageSquarePlus, X } from "lucide-react";
import { type RefObject, useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { AnimatedLogo } from "@/components/brand/AnimatedLogo";
import { PoweredBy } from "@/components/shell/PoweredBy";
import { Button } from "@/components/ui/button";
import { toPlainText } from "@/lib/markdown";
import { cobStrings as t } from "@/lib/strings/cob";
import type { CobChat } from "./api";
import { CobComposer } from "./CobComposer";
import { CobCostNote } from "./CobCostNote";
import { CobHistory } from "./CobHistory";
import { CobMessage } from "./CobMessage";
import { stageText } from "./CobStageLine";
import { CobStarters } from "./CobStarters";
import { ContactProvider } from "./ContactProvider"; // TV3

/** What the polite live region says: the stage while answering, then the final answer once. */
export function announcement(chat: Pick<CobChat, "turns">): string {
	const last = chat.turns[chat.turns.length - 1];
	if (!last) return "";
	if (last.status === "streaming") return stageText(last.stage);
	if (!last.live) return "";
	if (last.status === "error") return last.error?.detail || t.errors.generic;
	return last.text ? t.answered.replace("{text}", toPlainText(last.text)) : "";
}

/**
 * COB panel body: header (logo, History, New chat, Close), the greeting and the screen's
 * starters while the chat is empty, the turns, the question box, the cost note and the footer.
 * The container (docked aside or phone sheet) belongs to the launcher.
 */
export function CobPanel({
	chat,
	route,
	onClose,
	inputRef,
	prefill,
}: {
	chat: CobChat;
	route: string;
	onClose: () => void;
	inputRef: RefObject<HTMLTextAreaElement | null>;
	prefill: { text: string; nonce: number } | null;
}) {
	const [view, setView] = useState<"chat" | "history">("chat");
	const body = useRef<HTMLDivElement>(null);
	const last = chat.turns[chat.turns.length - 1];

	// Keep the newest words in view while an answer streams, unless the person scrolled up.
	const lastText = last?.text;
	const turnCount = chat.turns.length;
	// biome-ignore lint/correctness/useExhaustiveDependencies: re-run when the text or the turn count changes
	useEffect(() => {
		const el = body.current;
		if (!el) return;
		if (el.scrollHeight - el.scrollTop - el.clientHeight < 120) el.scrollTop = el.scrollHeight;
	}, [lastText, turnCount]);

	const send = (m: string) => {
		setView("chat");
		void chat.send(m);
	};
	const open = (id: string) => {
		chat.loadConversation(id).then(
			() => setView("chat"),
			() => toast.error(t.loadFailed),
		);
	};

	return (
		<div className="flex h-full min-h-0 flex-col bg-background text-foreground">
			<header className="flex items-center gap-2 border-b border-border px-3 py-2">
				<AnimatedLogo className="size-9 shrink-0" />
				<h2 className="m-0 flex-1 text-[15px] font-bold">{t.name}</h2>
				<Button
					type="button"
					size="icon-sm"
					variant="ghost"
					aria-label={view === "history" ? t.historyBack : t.history}
					aria-pressed={view === "history"}
					onClick={() => setView((v) => (v === "history" ? "chat" : "history"))}
				>
					<History aria-hidden />
				</Button>
				<Button
					type="button"
					size="icon-sm"
					variant="ghost"
					aria-label={t.newChat}
					onClick={() => {
						chat.reset();
						setView("chat");
						inputRef.current?.focus();
					}}
				>
					<MessageSquarePlus aria-hidden />
				</Button>
				<Button type="button" size="icon-sm" variant="ghost" aria-label={t.close} onClick={onClose}>
					<X aria-hidden />
				</Button>
			</header>

			<div role="status" aria-live="polite" className="sr-only" data-testid="cob-live">
				{announcement(chat)}
			</div>

			{view === "history" ? (
				<div className="min-h-0 flex-1 overflow-y-auto">
					<CobHistory
						currentId={chat.conversationId}
						onOpen={open}
						onDeleted={(id) => {
							if (id === chat.conversationId) chat.reset();
						}}
					/>
				</div>
			) : (
				<div ref={body} className="grid min-h-0 flex-1 content-start gap-4 overflow-y-auto p-3" data-testid="cob-turns">
					{chat.turns.length === 0 ? (
						<div className="grid gap-3">
							<p className="m-0 text-[14px] text-foreground-2">{t.greeting}</p>
							<CobStarters route={route} onPick={send} />
						</div>
					) : (
						chat.turns.map((turn, i) => (
							<CobMessage
								// biome-ignore lint/suspicious/noArrayIndexKey: turns are append-only within a chat
								key={i}
								turn={turn}
								latest={i === chat.turns.length - 1}
								busy={chat.busy}
								onSend={send}
								onRetry={chat.retry}
								onRated={chat.markRated}
							/>
						))
					)}
				</div>
			)}

			<CobComposer busy={chat.busy} onSend={send} onStop={chat.stop} inputRef={inputRef} prefill={prefill} />
			<div className="grid gap-1 px-3 pb-2">
				<CobCostNote />
				<ContactProvider route={route} onNavigate={onClose} /> {/* TV3 */}
				<PoweredBy tone="auth" />
			</div>
		</div>
	);
}
