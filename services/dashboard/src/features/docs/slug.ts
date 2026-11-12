/** Docs URLs: `/docs/<page slug>#<section anchor>`; the slug is the splat of `/docs/$`. */

export const DOCS_ROOT = "/docs";

export function docsHref(slug: string, anchor?: string): string {
	return `${DOCS_ROOT}/${slug}${anchor ? `#${anchor}` : ""}`;
}

/** The slug in a splat: no leading or trailing slashes, "" for the Docs home. */
export function slugFromSplat(splat: string | undefined): string {
	return (splat ?? "").replace(/^\/+|\/+$/g, "");
}

/** `/docs/screens/widget#embed` -> its slug and anchor; null for any other path. */
export function parseDocsHref(href: string): { slug: string; anchor: string } | null {
	const [path = "", anchor = ""] = href.split("#", 2);
	if (path !== DOCS_ROOT && !path.startsWith(`${DOCS_ROOT}/`)) return null;
	return { slug: slugFromSplat(path.slice(DOCS_ROOT.length)), anchor };
}
