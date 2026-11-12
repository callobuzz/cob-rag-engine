import { useBranding } from "@/lib/branding/BrandingProvider";
import { cn } from "@/lib/utils";

/**
 * "cob-rag-engine by Callobuzz" — on every public page and at the bottom of every signed-in page.
 * Plain text, never a link. The text comes from branding `powered_by` (constant, not a setting) or
 * the same built-in constant when branding failed. Shown by default; the operator can hide it in Branding
 * (`hide_powered_by`, so `powered_by` is null). There is no prop to restyle it: the only prop picks the surface.
 */
export interface PoweredByProps {
	tone?: "page" | "auth";
}

export function PoweredBy({ tone = "page" }: PoweredByProps) {
	const { poweredBy } = useBranding();
	if (!poweredBy) return null; // hidden when the operator hides it in Branding
	return (
		<p
			data-testid="powered-by"
			className={cn("m-0 text-center text-[12.5px] text-muted-foreground", tone === "page" ? "py-6" : "pt-2")}
		>
			<span>{poweredBy.text}</span>
		</p>
	);
}
