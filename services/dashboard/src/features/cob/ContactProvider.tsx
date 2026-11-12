import { Link } from "@tanstack/react-router";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { supportStrings } from "@/lib/strings/support";

/**
 * "Still stuck? Contact {product}" at the foot of COB's panel (TV3): for a business's own
 * dashboard, a link to the support page with the page the person was on, so the request carries it.
 * Never shown to the provider's own view (they have no one to write to).
 */
export function ContactProvider({ route, onNavigate }: { route: string; onNavigate?: () => void }) {
	const principal = usePrincipal();
	const { productName } = useBranding();
	if (principal?.view !== "tenant" || principal.auth !== "session") return null;
	return (
		<p data-testid="cob-contact-provider" className="m-0 text-[12.5px] text-muted-foreground">
			{supportStrings.footer.stuck}{" "}
			<Link
				to="/support"
				search={{ new: 1, page: route.startsWith("/") && !route.startsWith("/support") ? route : undefined }}
				onClick={onNavigate}
				className="font-semibold underline"
			>
				{fill(supportStrings.footer.contact, { product: productName })}
			</Link>
		</p>
	);
}
