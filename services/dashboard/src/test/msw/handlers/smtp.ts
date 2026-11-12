import { HttpResponse, http } from "msw";
import type { SmtpStatus } from "@/features/email/api";
import { BASE } from "../data";
import { coded } from "./auth";

/** A2 `GET /admin/smtp` and `POST /admin/smtp/test`, per provider, with every coded outcome. */
export function smtpStatus(provider: SmtpStatus["provider"], over: Partial<SmtpStatus> = {}): SmtpStatus {
	return {
		provider,
		configured: provider !== "disabled",
		host: provider === "smtp" ? "smtp.zoho.in" : null,
		port: 587,
		security: "starttls",
		from_address: provider === "smtp" ? "Sahayak AI <hello@sahayak.ai>" : null,
		username_set: provider === "smtp",
		password_set: provider === "smtp",
		dashboard_url: "http://localhost:3000",
		...over,
	};
}

export function createSmtpMock(
	provider: SmtpStatus["provider"] = "smtp",
	test: "ok" | "failed" | "timeout" = "ok",
	over: Partial<SmtpStatus> = {},
) {
	const sent: string[] = [];
	const handlers = [
		http.get(`${BASE}/api/v1/admin/smtp`, () => HttpResponse.json(smtpStatus(provider, over))),
		http.post(`${BASE}/api/v1/admin/smtp/test`, async ({ request }) => {
			const { to } = (await request.json()) as { to: string };
			if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(to))
				return HttpResponse.json(
					{ detail: [{ loc: ["body", "to"], msg: "value is not a valid email address", type: "value_error" }] },
					{ status: 422 },
				);
			if (provider === "disabled")
				return coded(503, "email_not_configured", { detail: "E-mail is not configured (set RAG_SMTP_HOST)" });
			if (test === "failed")
				return coded(502, "smtp_test_failed", {
					detail: "The SMTP server refused the message",
					smtp_code: 535,
					error: "SMTPAuthenticationError",
				});
			if (test === "timeout")
				return coded(502, "smtp_test_failed", {
					detail: "The test e-mail could not be sent",
					smtp_code: null,
					error: "TimeoutError",
				});
			sent.push(to);
			return HttpResponse.json({ sent: true, provider: provider === "log" ? "log" : "smtp", to });
		}),
	];
	return { handlers, sent };
}
