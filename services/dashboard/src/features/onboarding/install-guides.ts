import { fill } from "@/lib/strings/en";
import { onboardingStrings as t } from "@/lib/strings/onboarding";

export const PLATFORMS = ["any", "wordpress", "wix", "shopify", "squarespace", "google", "developer"] as const;
export type Platform = (typeof PLATFORMS)[number];

/** The "Send to my developer" text: three steps and the code, written for someone who is not us. */
export function developerMessage(snippet: string, website: string | null): string {
	return fill(t.two.developer.body, { website: website ?? "", code: snippet });
}

/** `mailto:` link with the message in the body. Built here, in the browser: the engine sends nothing. */
export function developerMailto(snippet: string, website: string | null): string {
	const subject = encodeURIComponent(t.two.developer.subject);
	const body = encodeURIComponent(developerMessage(snippet, website));
	return `mailto:?subject=${subject}&body=${body}`;
}

/** `https://wa.me/?text=` link: WhatsApp opens with the message ready to send to anyone. */
export function developerWhatsapp(snippet: string, website: string | null): string {
	return `https://wa.me/?text=${encodeURIComponent(`${t.two.developer.subject}\n\n${developerMessage(snippet, website)}`)}`;
}

/** The host of a website address for words like "Connected. Your assistant is live on shreedental.in". */
export function siteName(origin: string | null | undefined): string {
	return (origin ?? "").replace(/^https?:\/\//, "").replace(/\/$/, "");
}
