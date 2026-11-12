import { useState } from "react";
import type { Cta } from "./CtaButton";
import { CtaButton } from "./CtaButton";
import type { QuickReply } from "./QuickReplyChips";
import { QuickReplyChips } from "./QuickReplyChips";

/**
 * What an assistant answer offers under it: suggestion chips (only on the newest answer, so old
 * ones do not pile up) and the one call-to-action button, which "Not now" hides for this answer.
 * The playground and the preview console have no website-chat session, so no event is sent.
 */
export function ReplyExtras({
	replies,
	cta,
	latest,
	disabled,
	onSend,
}: {
	replies: readonly QuickReply[];
	cta: Cta | null | undefined;
	latest: boolean;
	disabled: boolean;
	onSend: (message: string) => void;
}) {
	const [dismissed, setDismissed] = useState(false);
	const showCta = cta && !dismissed;
	if (!(latest && replies.length) && !showCta) return null;
	return (
		<div data-testid="reply-extras">
			{showCta ? <CtaButton cta={cta} onDismiss={() => setDismissed(true)} /> : null}
			{latest ? <QuickReplyChips items={replies} onPick={onSend} disabled={disabled} /> : null}
		</div>
	);
}
