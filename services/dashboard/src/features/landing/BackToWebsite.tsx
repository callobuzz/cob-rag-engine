import { ArrowLeft } from "lucide-react";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { landingStrings as t } from "@/lib/strings/landing";

/**
 * "Back to the website" on the sign-in and sign-up pages, only while the public website is live.
 * The website is at the root of this address, so it is a plain link (a full page load, not the
 * dashboard's router).
 */
export function BackToWebsite() {
	const { landing, productName } = useBranding();
	if (!landing) return null;
	return (
		<a
			href="/"
			data-testid="back-to-website"
			className="inline-flex w-fit items-center gap-1.5 text-[13px] font-medium text-foreground-2 no-underline hover:text-foreground"
		>
			<ArrowLeft className="size-3.5" aria-hidden />
			{fill(t.backToWebsite, { product: productName })}
		</a>
	);
}
