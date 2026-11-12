import { ThumbsDown, ThumbsUp } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { cobStrings as t } from "@/lib/strings/cob";
import { FEEDBACK_COMMENT_MAX, useCobFeedback } from "./api";

/**
 * Thumbs on a finished answer. Up posts at once; down opens an optional comment (500 characters)
 * and posts on Send or Skip. One rating per answer: afterwards a thank-you line replaces them.
 */
export function CobFeedback({
	messageId,
	rating,
	onRated,
}: {
	messageId: string;
	rating: 1 | -1 | null;
	onRated: (messageId: string, rating: 1 | -1) => void;
}) {
	const fb = useCobFeedback();
	const [commenting, setCommenting] = useState(false);
	const [comment, setComment] = useState("");

	if (rating) {
		return (
			<p role="status" className="m-0 text-[12px] text-success">
				{t.feedback.thanks}
			</p>
		);
	}
	const submit = (value: 1 | -1, text?: string) =>
		fb.mutate(
			{ messageId, rating: value, comment: text },
			{
				onSuccess: () => {
					setCommenting(false);
					onRated(messageId, value);
				},
				onError: () => toast.error(t.feedback.failed),
			},
		);

	return (
		<div className="grid gap-2" data-testid="cob-feedback">
			<div className="flex items-center gap-1">
				<Button
					type="button"
					size="icon-sm"
					variant="ghost"
					aria-label={t.feedback.up}
					disabled={fb.isPending}
					onClick={() => submit(1)}
				>
					<ThumbsUp aria-hidden />
				</Button>
				<Button
					type="button"
					size="icon-sm"
					variant="ghost"
					aria-label={t.feedback.down}
					aria-expanded={commenting}
					disabled={fb.isPending}
					onClick={() => setCommenting(true)}
				>
					<ThumbsDown aria-hidden />
				</Button>
			</div>
			{commenting ? (
				<div className="grid gap-2">
					<label htmlFor={`cob-fb-${messageId}`} className="text-[12px] text-foreground-2">
						{t.feedback.commentLabel}
					</label>
					<Textarea
						id={`cob-fb-${messageId}`}
						value={comment}
						maxLength={FEEDBACK_COMMENT_MAX}
						placeholder={t.feedback.commentPlaceholder}
						onChange={(e) => setComment(e.target.value)}
						className="min-h-[56px] text-[13px]"
					/>
					<div className="flex gap-2">
						<Button type="button" size="sm" variant="brand" disabled={fb.isPending} onClick={() => submit(-1, comment)}>
							{t.feedback.send}
						</Button>
						<Button type="button" size="sm" variant="ghost" disabled={fb.isPending} onClick={() => submit(-1)}>
							{t.feedback.skip}
						</Button>
					</div>
				</div>
			) : null}
		</div>
	);
}
