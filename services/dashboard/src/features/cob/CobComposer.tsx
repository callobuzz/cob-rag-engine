import { Send, Square } from "lucide-react";
import { type KeyboardEvent, type RefObject, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { cobStrings as t } from "@/lib/strings/cob";
import { fill } from "@/lib/strings/en";
import { MESSAGE_MAX } from "./api";

interface Values {
	message: string;
}

/**
 * The question box: Enter sends, Shift+Enter adds a line, a counter shows the 2000-character
 * limit, Stop replaces Send while COB is answering. `prefill` puts text in the box (a starter or
 * "Ask COB about this page" with no auto-send); a new `nonce` re-applies it.
 */
export function CobComposer({
	busy,
	onSend,
	onStop,
	inputRef,
	prefill,
}: {
	busy: boolean;
	onSend: (message: string) => void;
	onStop: () => void;
	inputRef: RefObject<HTMLTextAreaElement | null>;
	prefill?: { text: string; nonce: number } | null;
}) {
	const form = useAppForm<Values>({ defaultValues: { message: "" } });
	const submit = useAppSubmit(form, ({ message }) => {
		const text = message.trim();
		if (!text) return;
		form.reset({ message: "" });
		onSend(text);
	});
	const reg = form.register("message", { maxLength: MESSAGE_MAX });
	const value = form.watch("message");

	const nonce = prefill?.nonce;
	const prefillText = prefill?.text;
	useEffect(() => {
		if (nonce === undefined || prefillText === undefined) return;
		form.setValue("message", prefillText.slice(0, MESSAGE_MAX));
		inputRef.current?.focus();
	}, [nonce, prefillText, form, inputRef]);

	const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
		if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
			e.preventDefault();
			if (!busy) void submit.onSubmit();
		}
	};

	return (
		<form onSubmit={submit.onSubmit} className="grid gap-1.5 border-t border-border p-3" data-testid="cob-composer">
			<label htmlFor="cob-input" className="sr-only">
				{t.composer.label}
			</label>
			<Textarea
				id="cob-input"
				{...reg}
				ref={(el) => {
					reg.ref(el);
					inputRef.current = el;
				}}
				rows={2}
				maxLength={MESSAGE_MAX}
				placeholder={t.composer.placeholder}
				aria-describedby="cob-input-hint"
				onKeyDown={onKeyDown}
				className="max-h-40 min-h-[56px] resize-none text-[13.5px]"
			/>
			<div className="flex items-center justify-between gap-2">
				<span id="cob-input-hint" className="text-[11.5px] text-muted-foreground">
					<span className="max-[860px]:hidden">{t.composer.hint} · </span>
					{fill(t.composer.count, { n: value.length, max: MESSAGE_MAX })}
				</span>
				{busy ? (
					<Button type="button" size="sm" variant="outline" onClick={onStop}>
						<Square aria-hidden />
						{t.composer.stop}
					</Button>
				) : (
					<Button type="submit" size="sm" variant="brand" disabled={!value.trim()}>
						<Send aria-hidden />
						{t.composer.send}
					</Button>
				)}
			</div>
		</form>
	);
}
