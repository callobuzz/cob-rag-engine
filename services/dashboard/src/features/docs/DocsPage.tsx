import { useQuery } from "@tanstack/react-query";
import { Link, useLocation, useRouter } from "@tanstack/react-router";
import { ArrowLeft, ArrowRight, ExternalLink, SearchX } from "lucide-react";
import { useEffect } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { buttonVariants } from "@/components/ui/button";
import { isApiError } from "@/lib/api/errors";
import { useRagUrl } from "@/lib/auth/store";
import { Markdown } from "@/lib/markdown";
import { docsStrings as t } from "@/lib/strings/docs";
import { cn } from "@/lib/utils";
import { AskCobButton } from "./AskCobButton";
import { docsPageQuery, fillBaseUrl } from "./api";
import { DocsToc } from "./DocsToc";
import { OpenScreenLink } from "./OpenScreenLink";

/** Brings the section named in the address bar into view and moves focus to it. */
function useScrollToHash(ready: boolean, hash: string) {
	useEffect(() => {
		if (!ready || !hash) return;
		const el = document.getElementById(decodeURIComponent(hash));
		if (!el) return;
		el.setAttribute("tabindex", "-1");
		el.focus({ preventScroll: true });
		el.scrollIntoView({ block: "start" });
	}, [ready, hash]);
}

export function DocsNotFound() {
	return (
		<section
			data-testid="docs-not-found"
			className="grid justify-items-center gap-3 rounded-lg border border-border bg-card px-6 py-12 text-center"
		>
			<SearchX className="size-8 text-muted-foreground" aria-hidden />
			<h2 className="m-0 text-lg font-bold">{t.notFoundTitle}</h2>
			<p className="m-0 max-w-[46ch] text-foreground-2">{t.notFoundBody}</p>
			<Link to="/docs/$" params={{ _splat: "" }} className={cn(buttonVariants({ variant: "outline" }), "no-underline")}>
				{t.backToDocs}
			</Link>
		</section>
	);
}

/** One Docs page: heading, actions, the page as Markdown with the engine address filled in, neighbours. */
export function DocsPage({ slug }: { slug: string }) {
	const router = useRouter();
	const hash = useLocation({ select: (l) => l.hash });
	const ragUrl = useRagUrl();
	const page = useQuery(docsPageQuery(slug));
	useScrollToHash(page.isSuccess, hash);

	if (page.isPending) {
		return (
			<div role="status" aria-busy="true" className="grid gap-4">
				<span className="sr-only">{t.loadingPage}</span>
				<CardSkeleton lines={1} />
				<CardSkeleton lines={6} />
			</div>
		);
	}
	if (page.isError) {
		if (isApiError(page.error) && page.error.code === "docs_page_not_found") return <DocsNotFound />;
		return <ErrorState error={page.error} title={t.pageFailed} onRetry={() => void page.refetch()} />;
	}

	const doc = page.data;
	const jump = (anchor: string) => {
		const base = router.state.location.pathname + router.state.location.searchStr;
		void router.navigate({ href: `${base}#${anchor}` });
	};
	const open = (href: string) => {
		if (href.startsWith("#")) jump(href.slice(1));
		else void router.navigate({ href });
	};

	return (
		<div className="grid gap-6 min-[1200px]:grid-cols-[minmax(0,1fr)_200px]">
			<article className="min-w-0 max-w-[760px]" aria-labelledby="docs-title">
				<h2 id="docs-title" className="m-0 text-[28px] font-bold break-words" tabIndex={-1}>
					{doc.title}
				</h2>
				{doc.summary ? <p className="mt-2 mb-0 text-foreground-2">{doc.summary}</p> : null}
				<div className="mt-3 flex flex-wrap items-center gap-2">
					<OpenScreenLink route={doc.route} />
					<AskCobButton slug={doc.slug} title={doc.title} />
					{doc.public_url ? (
						<a
							href={doc.public_url}
							target="_blank"
							rel="noopener noreferrer"
							className={cn(buttonVariants({ variant: "ghost", size: "sm" }), "no-underline")}
						>
							{t.publicCopy}
							<ExternalLink aria-hidden />
						</a>
					) : null}
				</div>
				<div className="mt-4">
					<DocsToc items={doc.toc} activeAnchor={hash} onJump={jump} variant="disclosure" />
				</div>
				<div className="mt-4">
					<Markdown
						text={fillBaseUrl(doc.markdown, ragUrl)}
						variant="page"
						headingIds={doc.toc.map((x) => x.anchor)}
						onNavigate={open}
					/>
				</div>
				{doc.prev || doc.next ? (
					<nav
						aria-label={t.pagerLabel}
						className="mt-8 grid grid-cols-2 gap-3 border-t border-border pt-4 text-[13.5px]"
					>
						{doc.prev ? (
							<Link
								to="/docs/$"
								params={{ _splat: doc.prev.slug }}
								className="flex items-center gap-2 rounded-md border border-border bg-card p-3 no-underline hover:bg-accent"
							>
								<ArrowLeft className="size-4 shrink-0" aria-hidden />
								<span className="min-w-0">
									<span className="block text-[11.5px] text-muted-foreground">{t.prev}</span>
									<span className="block truncate font-semibold text-foreground">{doc.prev.title}</span>
								</span>
							</Link>
						) : (
							<span />
						)}
						{doc.next ? (
							<Link
								to="/docs/$"
								params={{ _splat: doc.next.slug }}
								className="flex items-center justify-end gap-2 rounded-md border border-border bg-card p-3 text-right no-underline hover:bg-accent"
							>
								<span className="min-w-0">
									<span className="block text-[11.5px] text-muted-foreground">{t.next}</span>
									<span className="block truncate font-semibold text-foreground">{doc.next.title}</span>
								</span>
								<ArrowRight className="size-4 shrink-0" aria-hidden />
							</Link>
						) : null}
					</nav>
				) : null}
			</article>
			<DocsToc items={doc.toc} activeAnchor={hash} onJump={jump} variant="column" />
		</div>
	);
}
