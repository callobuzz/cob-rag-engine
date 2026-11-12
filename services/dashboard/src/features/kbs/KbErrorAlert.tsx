import { Link } from "@tanstack/react-router";
import { FormAlert } from "@/components/auth/FormAlert";
import { messageFor } from "@/lib/api/error-messages";
import { type ApiError, toApiError } from "@/lib/api/errors";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";

/**
 * A form-level alert for a failed KB call: the README message map, plus its in-app action link
 * ("See plan" for `kb_limit_reached`) unless `hideAction` (the super admin has no /plan page).
 */
export function KbErrorAlert({ error, hideAction }: { error: unknown; hideAction?: boolean }) {
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();
	const e: ApiError = toApiError(error);
	const msg = messageFor(e, { ragUrl, support: supportEmail });
	const to = msg.action?.to;
	return (
		<FormAlert>
			<b className="font-semibold">{msg.title}</b>
			{msg.body ? <span> {msg.body}</span> : null}
			{!hideAction && msg.action && to?.startsWith("/") ? (
				<span className="mt-1 block">
					<Link to={to} className="font-semibold text-destructive underline">
						{msg.action.label}
					</Link>
				</span>
			) : null}
		</FormAlert>
	);
}
