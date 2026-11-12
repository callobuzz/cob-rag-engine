import { useQuery } from "@tanstack/react-query";
import { brandingQuery, safeHttpUrl } from "@/lib/branding/BrandingProvider";
import { legalStrings as t } from "@/lib/strings/legal";
import { cn } from "@/lib/utils";

export interface FooterLink {
	slug: string;
	title: string;
	url: string;
}

/** The published pages the deployer wants in the footers (`legal` on the public branding). Only
 * http(s) addresses survive; nothing is shown while nothing is published. */
export function footerLinks(
	legal: ReadonlyArray<{ slug: string; title: string; url: string }> | undefined,
): FooterLink[] {
	return (legal ?? []).flatMap((l) => {
		const url = safeHttpUrl(l.url);
		return url ? [{ slug: l.slug, title: l.title, url }] : [];
	});
}

export function useFooterLinks(): FooterLink[] {
	const q = useQuery({ ...brandingQuery, select: (data) => footerLinks(data.legal) });
	return q.data ?? [];
}

/** Links to the published legal pages: under every public page and every signed-in page. They open
 * the engine's plain page in a new tab. */
export function LegalLinks({ tone = "page", only }: { tone?: "page" | "auth"; only?: readonly string[] }) {
	const links = useFooterLinks().filter((l) => !only || only.includes(l.slug));
	if (links.length === 0) return null;
	return (
		<nav
			aria-label={t.links.label}
			data-testid="legal-links"
			className={cn("text-center", tone === "page" ? "pt-4" : "pt-1")}
		>
			<ul className="m-0 flex list-none flex-wrap justify-center gap-x-4 gap-y-1 p-0 text-[12.5px]">
				{links.map((l) => (
					<li key={l.slug}>
						<a
							href={l.url}
							target="_blank"
							rel="noopener noreferrer"
							className="text-muted-foreground underline underline-offset-2 hover:text-foreground"
						>
							{l.title}
						</a>
					</li>
				))}
			</ul>
		</nav>
	);
}
