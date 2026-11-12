import { useMutation } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import type { components } from "@/lib/api/schema";

/**
 * E-mail status and test send (A2). `GET /admin/smtp` never returns the username or password,
 * only whether they are set. The status query is shared with D7's invitation dialogs.
 */
export { smtpQuery } from "@/features/customers/api";
export type SmtpStatus = components["schemas"]["SMTPStatusResponse"];

/** `POST /admin/smtp/test {to}`: sent synchronously; 503 `email_not_configured`, 502 `smtp_test_failed`. */
export function useSmtpTest() {
	return useMutation({
		mutationFn: (to: string) => unwrap(api().POST("/api/v1/admin/smtp/test", { body: { to } })),
	});
}
