import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { playgroundStrings as t } from "@/lib/strings/playground";
import { useSendFeedback } from "./api";

export const CORRECTION_MAX = 5000;

/**
 * 👍 = rating 5, 👎 = rating 1 with an optional correction (≤ 5000) → `POST /api/v1/feedback`.
 * One per answer: once sent the buttons are replaced by a thank-you line.
 */
export function FeedbackButtons({
	queryId,
	sent,
	onSent,
}: {
	queryId: string;
	sent: "up" | "down" | null;
	onSent: (v: "up" | "down") => void;
}) {
	const send = useSendFeedback();
	const [correcting, setCorrecting] = useState(false);
	const [correction, setCorrection] = useState("");
	const id = useId();
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();

	if (sent) {
		return (
			<p role="status" className="m-0 text-xs text-success">
				{t.feedback.thanks}
			</p>
		);
	}
	const submit = (rating: 1 | 5, text?: string) =>
		send.mutate(
			{ queryId, rating, correction: text },
			{
				onSuccess: () => {
					setCorrecting(false);
					onSent(rating === 5 ? "up" : "down");
				},
				onError: (e) => toast.error(messageFor(toApiError(e), { ragUrl, support: supportEmail }).title),
			},
		);

	return (
		<div className="grid gap-2">
			<div className="flex gap-1">
				<Button
					type="button"
					size="icon-sm"
					variant="ghost"
					aria-label={t.feedback.up}
					title={t.feedback.up}
					disabled={send.isPending}
					onClick={() => submit(5)}
				>
					<ThumbsUp aria-hidden />
				</Button>
				<Button
					type="button"
					size="icon-sm"
					variant="ghost"
					aria-label={t.feedback.down}
					title={t.feedback.down}
					aria-expanded={correcting}
					disabled={send.isPending}
					onClick={() => setCorrecting((v) => !v)}
				>
					<ThumbsDown aria-hidden />
				</Button>
			</div>
			{correcting ? (
				<form
					className="grid gap-2"
					onSubmit={(e) => {
						e.preventDefault();
						submit(1, correction);
					}}
				>
					<label htmlFor={id} className="text-xs font-semibold text-foreground-2">
						{t.feedback.correctionLabel}
					</label>
					<Textarea
						id={id}
						rows={3}
						maxLength={CORRECTION_MAX}
						value={correction}
						onChange={(e) => setCorrection(e.target.value)}
					/>
					<div className="flex gap-2">
						<Button type="submit" size="sm" variant="default" disabled={send.isPending}>
							{t.feedback.send}
						</Button>
						<Button type="button" size="sm" variant="ghost" onClick={() => setCorrecting(false)}>
							{t.feedback.cancel}
						</Button>
					</div>
				</form>
			) : null}
		</div>
	);
}
