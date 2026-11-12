import { Sparkles } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { fill } from "@/lib/strings/en";
import { setupStrings as t } from "@/lib/strings/setup";

/** Where a generated item came from: the crawl, the template, a confirmed detail or an answer. */
export function provenanceText(source: string | undefined, titles: Record<string, string> = {}): string {
	if (!source) return t.badges.unknown;
	if (source === "crawl") return t.badges.crawl;
	if (source === "template") return t.badges.template;
	if (source === "confirmed") return t.badges.confirmed;
	if (source === "existing") return t.badges.existing;
	if (source.startsWith("q:")) {
		const id = source.slice(2);
		return fill(t.badges.answer, { title: titles[id] ?? id });
	}
	return t.badges.unknown;
}

/**
 * A small badge with an icon and the plain-text source. Never fed model or page output through
 * Markdown: the text is built here from fixed strings and the question titles.
 */
export function ProvenanceBadge({ source, titles }: { source: string | undefined; titles?: Record<string, string> }) {
	const text = provenanceText(source, titles);
	return (
		<Badge
			variant={source?.startsWith("q:") ? "info" : "default"}
			className="h-auto min-h-[22px] whitespace-normal py-0.5"
		>
			<Sparkles aria-hidden />
			{text}
		</Badge>
	);
}
