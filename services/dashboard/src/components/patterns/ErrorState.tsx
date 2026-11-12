import { CircleAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { messageFor } from "@/lib/api/error-messages";
import { type ApiError, toApiError } from "@/lib/api/errors";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { en } from "@/lib/strings/en";

/** Error inside the card that failed (never a whole-page crash for one widget). */
export function ErrorState({ error, onRetry, title }: { error: unknown; onRetry?: () => void; title?: string }) {
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();
	const e: ApiError = toApiError(error);
	const msg = messageFor(e, { ragUrl, support: supportEmail });
	return (
		<div
			role="alert"
			className="flex items-start gap-3 rounded-md bg-destructive-soft p-3 text-[13px] text-destructive"
		>
			<CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
			<div className="grid flex-1 gap-1">
				<b className="font-semibold">{title ?? msg.title}</b>
				{title ? <span>{msg.title}</span> : null}
				{msg.body ? <span>{msg.body}</span> : null}
			</div>
			{onRetry ? (
				<Button size="sm" variant="outline" onClick={onRetry}>
					{en.common.retry}
				</Button>
			) : null}
		</div>
	);
}
