import { Link } from "@tanstack/react-router";
import { ArrowRight } from "lucide-react";
import { docsStrings as t } from "@/lib/strings/docs";
import { fill } from "@/lib/strings/en";
import type { DocsTree } from "./api";

const PREVIEW = 3;

/** `/docs`: a card per topic with its first pages, and the latest changelog entry. */
export function DocsHome({ tree }: { tree: DocsTree }) {
	const changelog = tree.topics.find((x) => x.id === "changelog")?.pages.find((p) => p.slug !== "changelog/intro");
	const topics = tree.topics.filter((x) => x.id !== "changelog");
	return (
		<div className="grid gap-6">
			<p className="m-0 max-w-[62ch] text-foreground-2">{t.homeIntro}</p>
			<ul className="m-0 grid list-none gap-3 p-0 sm:grid-cols-2 min-[1200px]:grid-cols-3">
				{topics.map((topic) => (
					<li key={topic.id} className="grid content-start gap-2 rounded-lg border border-border bg-card p-4">
						<h2 className="m-0 text-[15px] font-bold">{topic.title}</h2>
						<ul className="m-0 grid list-none gap-1 p-0 text-[13.5px]">
							{topic.pages.slice(0, PREVIEW).map((p) => (
								<li key={p.slug}>
									<Link to="/docs/$" params={{ _splat: p.slug }} className="text-brand-text">
										{p.title}
									</Link>
								</li>
							))}
						</ul>
						{topic.pages.length > PREVIEW ? (
							<Link
								to="/docs/$"
								params={{ _splat: topic.pages[0]?.slug ?? "" }}
								className="mt-1 inline-flex items-center gap-1 text-[12.5px] text-muted-foreground no-underline hover:text-foreground"
							>
								{fill(t.moreInTopic, { n: topic.pages.length })}
								<ArrowRight className="size-3" aria-hidden />
							</Link>
						) : null}
					</li>
				))}
			</ul>
			{changelog ? (
				<section aria-labelledby="docs-whats-new" className="grid gap-1 rounded-lg border border-border bg-card p-4">
					<h2 id="docs-whats-new" className="m-0 text-[15px] font-bold">
						{t.whatsNew}
					</h2>
					<p className="m-0 text-foreground-2">
						<b className="text-foreground">{changelog.title}</b>
						{changelog.summary ? ` — ${changelog.summary}` : null}
					</p>
					<Link to="/docs/$" params={{ _splat: changelog.slug }} className="text-brand-text">
						{t.whatsNewOpen}
					</Link>
				</section>
			) : null}
		</div>
	);
}
