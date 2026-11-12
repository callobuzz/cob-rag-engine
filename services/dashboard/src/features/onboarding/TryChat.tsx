import { Bot, Send, Square } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { isApiError } from "@/lib/api/errors";
import { avatarSrc } from "@/lib/avatar";
import { onboardingStrings } from "@/lib/strings/onboarding";
import { cn } from "@/lib/utils";
import { isAbort, streamAnswer } from "../playground/api";

const t = onboardingStrings.chat;

interface Line {
	id: number;
	role: "you" | "bot";
	text: string;
	status: "streaming" | "done" | "failed";
}

/**
 * A small chat with the business's own published assistant: the real answers, with the assistant's
 * own name and picture, from the same stream the playground uses. No widget and no website needed.
 * Suggested questions come in as buttons.
 */
export function TryChat({
	kb,
	name,
	avatarUrl,
	suggestions,
	learning,
}: {
	kb: string;
	name: string | null;
	avatarUrl: string | null;
	suggestions?: readonly string[];
	/** The first pass over the website is still running: say so, the answers will improve. */
	learning?: boolean;
}) {
	const [lines, setLines] = useState<Line[]>([]);
	const [text, setText] = useState("");
	const abort = useRef<AbortController | null>(null);
	const end = useRef<HTMLDivElement>(null);
	const next = useRef(1);
	const busy = lines.some((l) => l.status === "streaming");
	const label = name ?? t.unnamed;
	const picture = avatarSrc(avatarUrl);
	// biome-ignore lint/correctness/useExhaustiveDependencies: keep the newest line in view as it streams
	useEffect(() => {
		end.current?.scrollIntoView({ block: "nearest" });
	}, [lines]);
	useEffect(() => () => abort.current?.abort(), []);

	const patch = (id: number, change: Partial<Line> | ((l: Line) => Partial<Line>)) =>
		setLines((all) =>
			all.map((l) => (l.id === id ? { ...l, ...(typeof change === "function" ? change(l) : change) } : l)),
		);

	const ask = async (question: string) => {
		const q = question.trim();
		if (!q || busy) return;
		const you = next.current++;
		const bot = next.current++;
		setLines((all) => [
			...all,
			{ id: you, role: "you", text: q, status: "done" },
			{ id: bot, role: "bot", text: "", status: "streaming" },
		]);
		setText("");
		const controller = new AbortController();
		abort.current = controller;
		try {
			await streamAnswer(
				{ kb, query: q, signal: controller.signal },
				{
					onSources: () => {},
					onToken: (piece) => patch(bot, (l) => ({ text: l.text + piece })),
					onDone: () => patch(bot, { status: "done" }),
					onError: () => patch(bot, (l) => ({ status: l.text ? "done" : "failed" })),
				},
			);
			patch(bot, (l) => (l.status === "streaming" ? { status: l.text ? "done" : "failed" } : {}));
		} catch (e) {
			if (!isAbort(e)) patch(bot, { status: "failed", text: isApiError(e) && e.detail ? e.detail : "" });
			else patch(bot, { status: "done" });
		}
	};

	const submit = (e: FormEvent) => {
		e.preventDefault();
		void ask(text);
	};

	return (
		<div className="grid gap-3" data-testid="try-chat">
			<div className="flex items-center gap-3">
				{picture ? (
					<img src={picture} alt="" className="size-10 shrink-0 rounded-full border border-border object-cover" />
				) : (
					<span
						aria-hidden
						className="grid size-10 shrink-0 place-items-center rounded-full bg-secondary text-muted-foreground"
					>
						<Bot className="size-5" />
					</span>
				)}
				<b data-testid="try-chat-name" className="font-display text-[15px] font-bold break-words">
					{label}
				</b>
			</div>
			{learning ? <p className="m-0 text-[12.5px] text-muted-foreground">{t.learningNote}</p> : null}
			<div
				role="log"
				aria-live="polite"
				aria-busy={busy}
				aria-label={t.label}
				data-testid="try-chat-log"
				className="grid max-h-[320px] min-h-[140px] content-start gap-2 overflow-y-auto rounded-md border border-border bg-card p-3"
			>
				{lines.length === 0 ? <p className="m-0 text-[13px] text-muted-foreground">{t.empty}</p> : null}
				{lines.map((l) => (
					<div key={l.id} className={cn("grid gap-0.5", l.role === "you" && "justify-items-end")}>
						<span className="text-[11.5px] font-semibold text-muted-foreground">
							{l.role === "you" ? t.you : label}
						</span>
						<p
							className={cn(
								"m-0 max-w-[92%] rounded-lg px-3 py-2 text-[13.5px] whitespace-pre-wrap break-words",
								l.role === "you" ? "bg-primary text-primary-foreground" : "bg-secondary text-foreground",
								l.status === "failed" && "text-destructive",
							)}
						>
							{l.status === "failed" ? t.failed : l.text || "…"}
						</p>
					</div>
				))}
				<div ref={end} />
			</div>
			{suggestions?.length ? (
				<div className="flex flex-wrap gap-2" data-testid="suggestions">
					{suggestions.map((s) => (
						<Button key={s} type="button" size="sm" variant="outline" disabled={busy} onClick={() => void ask(s)}>
							{s}
						</Button>
					))}
				</div>
			) : null}
			<form onSubmit={submit} className="flex gap-2">
				<Input
					value={text}
					onChange={(e) => setText(e.target.value)}
					placeholder={t.placeholder}
					aria-label={t.placeholder}
					maxLength={2000}
					disabled={busy}
				/>
				{busy ? (
					<Button type="button" variant="outline" onClick={() => abort.current?.abort()}>
						<Square aria-hidden />
						{t.stop}
					</Button>
				) : (
					<Button type="submit" disabled={!text.trim()}>
						<Send aria-hidden />
						{t.send}
					</Button>
				)}
			</form>
		</div>
	);
}
