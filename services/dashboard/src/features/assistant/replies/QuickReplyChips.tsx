import { Button } from "@/components/ui/button";
import { assistantStrings as t } from "@/lib/strings/assistant";

export interface QuickReply {
	label: string;
	message: string;
}

/** Up to four suggestion chips under an answer (C6); a chip sends its message as the next question. */
export function QuickReplyChips({
	items,
	onPick,
	disabled,
}: {
	items: readonly QuickReply[];
	onPick: (message: string) => void;
	disabled?: boolean;
}) {
	if (!items.length) return null;
	return (
		<fieldset className="m-0 flex min-w-0 flex-wrap gap-1.5 border-0 p-0 pt-2">
			<legend className="sr-only">{t.replies.suggestions}</legend>
			{items.slice(0, 4).map((q) => (
				<Button
					key={q.message}
					type="button"
					size="sm"
					variant="outline"
					className="rounded-full"
					disabled={disabled}
					onClick={() => onPick(q.message)}
				>
					{q.label}
				</Button>
			))}
		</fieldset>
	);
}
