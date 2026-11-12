import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { docsStrings as t } from "@/lib/strings/docs";
import { cn } from "@/lib/utils";
import type { DocsTree } from "./api";

/** The topics and pages of the Docs for this role. The topic holding the current page is open. */
export function DocsNav({ tree, slug, onNavigate }: { tree: DocsTree; slug: string; onNavigate?: () => void }) {
	return (
		<nav aria-label={t.navLabel} className="grid gap-1 text-[13.5px]">
			{tree.topics.map((topic) => {
				const holdsCurrent = topic.pages.some((p) => p.slug === slug);
				return (
					<details key={topic.id} open={holdsCurrent || undefined} className="group">
						<summary className="flex cursor-pointer list-none items-center gap-1.5 rounded-sm px-2 py-1.5 font-semibold text-foreground hover:bg-accent [&::-webkit-details-marker]:hidden">
							<ChevronRight className="size-3.5 shrink-0 transition-transform group-open:rotate-90" aria-hidden />
							<span className="min-w-0 flex-1 truncate">{topic.title}</span>
							<span className="font-mono text-[11px] font-normal text-muted-foreground">{topic.pages.length}</span>
						</summary>
						<ul className="m-0 grid list-none gap-0.5 p-0 pt-0.5 pl-3">
							{topic.pages.map((p) => (
								<li key={p.slug}>
									<Link
										to="/docs/$"
										params={{ _splat: p.slug }}
										onClick={onNavigate}
										aria-current={p.slug === slug ? "page" : undefined}
										className={cn(
											"block rounded-sm border-l-2 px-2.5 py-1 text-foreground-2 no-underline hover:bg-accent hover:text-foreground",
											p.slug === slug
												? "border-primary bg-primary-soft font-semibold text-foreground"
												: "border-transparent",
										)}
									>
										{p.title}
									</Link>
								</li>
							))}
						</ul>
					</details>
				);
			})}
		</nav>
	);
}
