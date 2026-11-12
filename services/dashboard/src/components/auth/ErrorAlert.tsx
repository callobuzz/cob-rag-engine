import { Link } from "@tanstack/react-router";
import { useCallback, useState } from "react";
import { RetryAfter, useCountdown } from "@/components/patterns/RetryAfter";
import { type MessageContext, messageFor } from "@/lib/api/error-messages";
import { type ApiError, toApiError } from "@/lib/api/errors";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { FormAlert } from "./FormAlert";

/** Form-level error state with a Retry-After deadline (lockouts, rate limits). */
export function useApiErrorState() {
	const [error, setErrorState] = useState<ApiError | null>(null);
	const [until, setUntil] = useState<number | null>(null);
	const left = useCountdown(until);
	const set = useCallback((e: unknown): ApiError => {
		const err = toApiError(e);
		setErrorState(err);
		setUntil(err.retryAfterS != null && err.retryAfterS > 0 ? Date.now() + err.retryAfterS * 1000 : null);
		return err;
	}, []);
	const clear = useCallback(() => {
		setErrorState(null);
		setUntil(null);
	}, []);
	return { error, set, clear, until, locked: until !== null && left > 0 };
}

/** The form alert for an ApiError: message map + live countdown + optional action link. */
export function ErrorAlert({
	error,
	until,
	context,
	id,
	onAction,
}: {
	error: ApiError;
	until?: number | null;
	context?: MessageContext;
	id?: string;
	onAction?: (actionId: string) => void;
}) {
	const ragUrl = useRagUrl();
	const { supportEmail, passwordPolicy } = useBranding();
	const ctx: MessageContext = { ragUrl, support: supportEmail, policy: passwordPolicy, ...context };
	const left = useCountdown(until ?? null);
	const counting = !!until;
	const msg = messageFor(error, counting ? { ...ctx, retry: "{t}" } : ctx);
	return (
		<FormAlert id={id}>
			<b className="font-semibold">
				{counting ? (
					left > 0 ? (
						<RetryAfter until={until ?? null} template={msg.title} />
					) : (
						"You can try again now."
					)
				) : (
					msg.title
				)}
			</b>
			{msg.body ? <span> {msg.body}</span> : null}
			{msg.action ? (
				<span className="mt-1 block">
					{msg.action.to?.startsWith("/") ? (
						<Link to={msg.action.to} className="font-semibold text-destructive underline">
							{msg.action.label}
						</Link>
					) : msg.action.to ? (
						<a href={msg.action.to} target="_blank" rel="noopener noreferrer" className="font-semibold underline">
							{msg.action.label}
						</a>
					) : msg.action.id && onAction ? (
						<button type="button" className="font-semibold underline" onClick={() => onAction(msg.action?.id ?? "")}>
							{msg.action.label}
						</button>
					) : null}
				</span>
			) : null}
		</FormAlert>
	);
}
