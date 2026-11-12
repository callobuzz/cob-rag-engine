import { useQuery } from "@tanstack/react-query";
import { Skeleton } from "@/components/ui/skeleton";
import { cobStrings as t } from "@/lib/strings/cob";
import { cobStartersQuery } from "./api";

/** Questions for the screen the person is on (the help page's starters), shown while the chat is empty. */
export function CobStarters({ route, onPick }: { route: string; onPick: (question: string) => void }) {
	const q = useQuery(cobStartersQuery(route));
	if (q.isPending) {
		return (
			<div role="status" aria-busy="true" className="grid gap-2">
				<span className="sr-only">{t.startersLoading}</span>
				<Skeleton className="h-9 w-full" />
				<Skeleton className="h-9 w-4/5" />
			</div>
		);
	}
	const starters = q.data?.starters ?? [];
	if (!starters.length) return null;
	return (
		<ul aria-label={t.startersLabel} className="m-0 grid list-none gap-2 p-0">
			{starters.map((s) => (
				<li key={s}>
					<button
						type="button"
						onClick={() => onPick(s)}
						className="w-full rounded-md border border-border bg-card px-3 py-2 text-left text-[13px] text-foreground hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none"
					>
						{s}
					</button>
				</li>
			))}
		</ul>
	);
}
