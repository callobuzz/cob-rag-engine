import { useState } from "react";
import { toast } from "sonner";
import { RetryAfter, useCountdown } from "@/components/patterns/RetryAfter";
import { api, unwrap } from "@/lib/api/client";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { en } from "@/lib/strings/en";

export const RESEND_SECONDS = 60;

/**
 * "Resend code" with a 60 s countdown after each send (A4: one e-mail per 60 s); a 429 restarts
 * the countdown from Retry-After. The neutral toast never says whether an account exists.
 */
export function useResend(email: string, purpose: "verify" | "reset", startCounting = true) {
	const [until, setUntil] = useState<number | null>(() => (startCounting ? Date.now() + RESEND_SECONDS * 1000 : null));
	const left = useCountdown(until);
	const [sending, setSending] = useState(false);
	const send = async () => {
		if (!email) return;
		setSending(true);
		try {
			await unwrap(api().POST("/api/v1/auth/resend", { body: { email, purpose } }));
			toast.success(en.verify.resent);
			setUntil(Date.now() + RESEND_SECONDS * 1000);
		} catch (e) {
			const err = toApiError(e);
			if (err.retryAfterS) setUntil(Date.now() + err.retryAfterS * 1000);
			toast.error(messageFor(err).title);
		} finally {
			setSending(false);
		}
	};
	return { send, until, waiting: left > 0, sending };
}

export function ResendButton({ resend }: { resend: ReturnType<typeof useResend> }) {
	return (
		<button
			type="button"
			className="text-[13px] font-semibold text-brand-text hover:underline disabled:cursor-default disabled:font-medium disabled:text-muted-foreground disabled:no-underline"
			disabled={resend.waiting || resend.sending}
			onClick={() => void resend.send()}
		>
			{resend.waiting ? (
				<RetryAfter until={resend.until} format="clock" template={en.verify.resendIn.replace("{clock}", "{t}")} />
			) : (
				en.verify.resend
			)}
		</button>
	);
}
