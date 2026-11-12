import { z } from "zod";

/**
 * The one email rule, the same as the engine's (`accounts/email_rules.py`): an address must be
 * well formed on a real domain. Reserved names (.local, .test, .example, .localhost, .invalid)
 * never receive mail; they are accepted only on a development engine, which says so with
 * `special_use_emails_allowed` on `GET /api/v1/public/branding` (RAG_ENV=dev).
 */
export const SPECIAL_USE_TLDS = ["local", "test", "example", "localhost", "invalid"] as const;

/** The reserved top-level name an address ends in, or null for a real domain. */
export function specialUseTld(address: string): string | null {
	const domain = address
		.slice(address.lastIndexOf("@") + 1)
		.replace(/\.$/, "")
		.toLowerCase();
	const tld = domain.slice(domain.lastIndexOf(".") + 1);
	return domain.includes(".") && (SPECIAL_USE_TLDS as readonly string[]).includes(tld) ? tld : null;
}

export const EMAIL_MESSAGES = {
	required: "Enter your email.",
	invalid: "Enter a valid email address.",
	special: (tld: string) =>
		`Addresses ending in .${tld} cannot receive email. Use a real address like priya@yourcompany.in.`,
} as const;

/**
 * Trimmed, required, well formed, and (unless the engine is in development) not a reserved name.
 *
 * The reserved-name refusal is for addresses being CREATED (sign-up, invitations, branding).
 * Forms that only look up an existing account (sign-in, forgot, reset) pass `true`, so a user
 * whose address was made on a dev engine is never locked out of finding it.
 */
export function emailSchema(allowSpecialUse: boolean) {
	return z
		.string()
		.trim()
		.min(1, EMAIL_MESSAGES.required)
		.pipe(z.email(EMAIL_MESSAGES.invalid))
		.superRefine((value, ctx) => {
			const tld = specialUseTld(value);
			if (tld && !allowSpecialUse) ctx.addIssue({ code: "custom", message: EMAIL_MESSAGES.special(tld) });
		});
}
