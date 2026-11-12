import { Button } from "@/components/ui/button";
import { docsHref, useCobLinks } from "./links";
import type { CobLinkBlock } from "./types";

/** The deep-link button ("Open Website widget →"). Clicking it keeps the panel open. */
export function CobLinkButton({ block }: { block: CobLinkBlock }) {
	const { hasDocs, onInternalClick } = useCobLinks();
	const href = block.docs_slug && (hasDocs || !block.route) ? docsHref(block.docs_slug) : block.route;
	if (!href) return null;
	return (
		<Button asChild variant="brand" size="sm" className="w-fit">
			<a href={href} data-testid="cob-link" onClick={onInternalClick(href)}>
				{block.label}
			</a>
		</Button>
	);
}
