import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { websiteQuery } from "@/features/website/api";
import { formatRelative } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { knowledgeStrings } from "@/lib/strings/knowledge";

const t = knowledgeStrings.lastRead;

/**
 * TV5: under the Knowledge header, when the website was last read, with a way to read it again.
 * Shares the website tab's query, so it costs no extra request. Nothing shows until a site has
 * been read at least once.
 */
export function LastReadLine({ kbId }: { kbId: string }) {
	const site = useQuery(websiteQuery(kbId));
	const newest = (site.data?.sources ?? [])
		.map((s) => s.last_run_finished_at)
		.filter((v): v is string => !!v)
		.sort()
		.at(-1);
	if (!newest) return null;
	return (
		<p data-testid="website-last-read" className="m-0 text-[13px] text-foreground-2">
			{fill(t.text, { when: formatRelative(newest) })}
			{" · "}
			<Link to="/knowledge" search={{ tab: "website" }} className="font-semibold text-primary hover:underline">
				{t.again}
			</Link>
		</p>
	);
}
