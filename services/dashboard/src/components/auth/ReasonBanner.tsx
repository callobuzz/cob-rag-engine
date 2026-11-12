import { messageFor } from "@/lib/api/error-messages";
import { ApiError } from "@/lib/api/errors";
import { FormAlert } from "./FormAlert";

/** `?reason=<code>` banner after a sign-out (README "Sign-out codes"). */
export function ReasonBanner({ reason }: { reason?: string }) {
	if (!reason) return null;
	const msg = messageFor(new ApiError({ status: 401, code: reason, detail: "" }));
	return (
		<FormAlert tone="warn">
			{msg.title}
			{msg.body ? ` ${msg.body}` : ""}
		</FormAlert>
	);
}
