import { useId, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { REPLY_MAX, replyState, useReply } from "./api";

/**
 * The reply box (TV6): write to the customer yourself, text only, up to 4,096 characters, and only
 * inside WhatsApp's 24-hour window (the reason is shown when it is closed). Sending also keeps the
 * assistant quiet in this chat for the number's pause hours.
 */
export function ReplyBox({ kb, threadId, windowOpen }: { kb: string; threadId: string; windowOpen: boolean }) {
	const id = useId();
	const reply = useReply(kb, threadId);
	const [text, setText] = useState("");
	const [failure, setFailure] = useState<string | null>(null);
	const state = replyState(text);

	if (!windowOpen) {
		return (
			<div className="grid gap-1 rounded-md border border-border bg-muted p-3" data-testid="reply-closed">
				<p className="m-0 text-[13.5px] font-semibold">{t.reply.closedTitle}</p>
				<p className="m-0 text-[13px] text-foreground-2">{t.reply.closedBody}</p>
			</div>
		);
	}

	const send = (e: React.FormEvent) => {
		e.preventDefault();
		if (state.empty || state.tooLong || reply.isPending) return;
		setFailure(null);
		reply.mutate(text, {
			onSuccess: () => {
				setText("");
				toast.success(t.reply.sent);
			},
			onError: (err) => {
				const e2 = toApiError(err);
				setFailure(e2.code === "whatsapp_window_closed" ? t.reply.windowClosed : messageFor(e2).title);
			},
		});
	};

	return (
		<form onSubmit={send} noValidate className="grid gap-2" data-testid="reply-box">
			{failure ? <FormAlert>{failure}</FormAlert> : null}
			<label htmlFor={id} className="text-[13px] font-semibold">
				{t.reply.label}
			</label>
			<Textarea
				id={id}
				rows={3}
				value={text}
				placeholder={t.reply.placeholder}
				aria-invalid={state.tooLong ? true : undefined}
				aria-describedby={`${id}-count`}
				onChange={(e) => setText(e.target.value)}
			/>
			<div className="flex items-center justify-between gap-3">
				<small
					id={`${id}-count`}
					data-testid="reply-count"
					className={state.tooLong ? "font-medium text-destructive" : "text-muted-foreground"}
				>
					{state.tooLong ? t.reply.tooLong : fill(t.reply.count, { n: formatNumber(Math.min(state.count, REPLY_MAX)) })}
				</small>
				<Button
					type="submit"
					variant="default"
					aria-disabled={state.empty || state.tooLong || reply.isPending || undefined}
				>
					{reply.isPending ? t.reply.sending : t.reply.send}
				</Button>
			</div>
		</form>
	);
}
