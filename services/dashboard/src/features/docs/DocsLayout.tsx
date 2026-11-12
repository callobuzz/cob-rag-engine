import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useRouter } from "@tanstack/react-router";
import { ListTree } from "lucide-react";
import { useCallback, useRef, useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetTitle } from "@/components/ui/sheet";
import { isApiError } from "@/lib/api/errors";
import { docsStrings as t } from "@/lib/strings/docs";
import { fill } from "@/lib/strings/en";
import { docsTreeQuery } from "./api";
import { DocsHome } from "./DocsHome";
import { DocsNav } from "./DocsNav";
import { DocsPage } from "./DocsPage";
import { DocsResults } from "./DocsResults";
import { DocsSearch } from "./DocsSearch";
import { slugFromSplat } from "./slug";

const route = getRouteApi("/_app/docs/$");

/** "0.1.0+abc123def456" -> "0.1.0" and the build hash. */
function splitVersion(version: string): { engine: string; hash: string } {
	const [engine = version, hash = ""] = version.split("+", 2);
	return { engine, hash };
}

/**
 * The Docs section (`/docs` and `/docs/<slug>`): topic navigation, search, then the home, a search
 * result list or one page. Every role sees it; the engine decides which pages each role gets.
 */
export function DocsLayout() {
	const { _splat } = route.useParams();
	const { q = "" } = route.useSearch();
	const router = useRouter();
	const slug = slugFromSplat(_splat);
	const tree = useQuery(docsTreeQuery);
	const searchRef = useRef<HTMLInputElement | null>(null);
	const [drawer, setDrawer] = useState(false);

	const setQuery = useCallback(
		(next: string) => {
			const clean = next.trim();
			const here = router.state.location;
			if ((here.search as { q?: string }).q === (clean || undefined)) return;
			void router.navigate({
				href: `${here.pathname}${clean ? `?q=${encodeURIComponent(clean)}` : ""}`,
				replace: true,
			});
		},
		[router],
	);

	const version = tree.data ? splitVersion(tree.data.version) : null;
	const badge = version ? (
		<span
			title={fill(t.versionTip, { hash: version.hash })}
			className="inline-flex items-center rounded-full border border-border bg-muted px-2.5 py-1 font-mono text-[11.5px] text-muted-foreground"
		>
			{fill(t.versionBadge, { version: version.engine })}
		</span>
	) : null;

	let body: React.ReactNode;
	if (q.trim()) body = <DocsResults q={q} />;
	else if (slug) body = <DocsPage slug={slug} />;
	else if (tree.data) body = <DocsHome tree={tree.data} />;
	else body = null;

	return (
		<>
			<PageHeader title={t.title} description={t.description} actions={badge} />
			{tree.isPending ? (
				<div role="status" aria-busy="true" className="grid gap-4">
					<span className="sr-only">{t.loading}</span>
					<CardSkeleton lines={1} />
					<CardSkeleton lines={5} />
				</div>
			) : tree.isError ? (
				<ErrorState
					error={tree.error}
					title={isApiError(tree.error) && tree.error.code === "session_required" ? t.needsAccount : t.loadFailed}
					onRetry={() => void tree.refetch()}
				/>
			) : (
				<div className="grid items-start gap-6 min-[1024px]:grid-cols-[250px_minmax(0,1fr)]">
					<aside className="hidden min-[1024px]:sticky min-[1024px]:top-4 min-[1024px]:block min-[1024px]:max-h-[calc(100dvh-8rem)] min-[1024px]:overflow-y-auto">
						<DocsNav tree={tree.data} slug={slug} />
					</aside>
					<div className="grid min-w-0 gap-4">
						<div className="flex flex-wrap items-center gap-2">
							<Button
								type="button"
								variant="outline"
								size="sm"
								className="min-[1024px]:hidden"
								onClick={() => setDrawer(true)}
							>
								<ListTree aria-hidden />
								{t.navToggle}
							</Button>
							<div className="min-w-[220px] flex-1 max-w-[520px]">
								<DocsSearch q={q} onQuery={setQuery} inputRef={searchRef} />
							</div>
						</div>
						{body}
					</div>
					<Sheet open={drawer} onOpenChange={setDrawer}>
						<SheetContent side="left" className="w-[300px] max-w-[88vw] gap-0 overflow-y-auto p-3">
							<SheetTitle className="sr-only">{t.navToggle}</SheetTitle>
							<SheetDescription className="sr-only">{t.description}</SheetDescription>
							<DocsNav tree={tree.data} slug={slug} onNavigate={() => setDrawer(false)} />
						</SheetContent>
					</Sheet>
				</div>
			)}
		</>
	);
}
