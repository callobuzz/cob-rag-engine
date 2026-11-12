import { useRouter } from "@tanstack/react-router";
import type { MouseEvent } from "react";
import type { CobSource } from "./types";

/** Where a click on a source or a link block goes. */
export type CobTarget = { kind: "internal"; href: string } | { kind: "external"; url: string } | null;

export const docsHref = (slug: string, anchor?: string | null): string => `/docs/${slug}${anchor ? `#${anchor}` : ""}`;

/**
 * A source opens its in-app Docs page when the dashboard has a Docs section (COB8), else its
 * public URL in a new tab, else the screen it describes.
 */
export function sourceTarget(src: CobSource, hasDocs: boolean): CobTarget {
	if (hasDocs && src.slug) return { kind: "internal", href: docsHref(src.slug, src.anchor) };
	if (src.public_url) return { kind: "external", url: src.public_url };
	if (src.route) return { kind: "internal", href: src.route };
	return null;
}

/**
 * Navigation for COB's links. The panel stays open (it lives in the shell, outside the routed
 * page), which is the point of a deep link. `hasDocs` is whether `/docs/$` exists in this build.
 */
export function useCobLinks() {
	const router = useRouter();
	const hasDocs = Boolean((router.routesByPath as unknown as Record<string, unknown>)["/docs/$"]);
	const go = (href: string) => {
		void router.navigate({ href });
	};
	/** `onClick` for a real anchor: modified clicks keep the browser's behaviour. */
	const onInternalClick = (href: string) => (e: MouseEvent<HTMLElement>) => {
		if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
		e.preventDefault();
		go(href);
	};
	return { hasDocs, go, onInternalClick };
}
