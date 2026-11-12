import { useQuery } from "@tanstack/react-query";
import { Ellipsis, ExternalLink } from "lucide-react";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { formatRelative, formatShortDate } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { can, LOCKED_TEXT } from "@/lib/permissions";
import { fill } from "@/lib/strings/en";
import { websiteStrings } from "@/lib/strings/website";
import { PAGE_SIZE, useExcludePage, useRereadPage, type WebsitePage, websitePagesQuery } from "./api";
import { pagePath, pageStatusWord, pageTitle, pageTone, skipReasonWord } from "./words";

const t = websiteStrings.pages;

/** The filter chips, in the order an owner cares about. "Waiting" is found-but-not-read. */
const CHIPS = [
	{ key: "learned", label: websiteStrings.pageStatus.learned, statuses: ["learned"] },
	{ key: "waiting", label: websiteStrings.pageStatus.queued, statuses: ["queued", "discovered"] },
	{ key: "skipped", label: websiteStrings.pageStatus.skipped, statuses: ["skipped"] },
	{ key: "excluded", label: websiteStrings.pageStatus.excluded, statuses: ["excluded"] },
	{ key: "failed", label: websiteStrings.pageStatus.failed, statuses: ["failed"] },
	{ key: "removed", label: websiteStrings.pageStatus.removed, statuses: ["removed"] },
] as const;

function toastError(e: unknown) {
	const m = messageFor(toApiError(e));
	toast.error(m.title, m.body ? { description: m.body } : undefined);
}

function RowMenu({ kb, page }: { kb: string; page: WebsitePage }) {
	const reread = useRereadPage(kb);
	const exclude = useExcludePage(kb);
	const principal = usePrincipal();
	const owner = can(principal, "website.manage");
	const title = pageTitle(page);
	const left = page.status === "excluded" || page.excluded_by_owner;
	return (
		<DropdownMenu modal={false}>
			<DropdownMenuTrigger asChild>
				<Button size="sm" variant="ghost" aria-label={fill(t.actionsFor, { title })}>
					<Ellipsis aria-hidden />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end">
				<DropdownMenuItem
					disabled={left}
					onSelect={() =>
						reread.mutate(page.id, {
							onSuccess: () => toast.success(fill(t.rereadQueued, { title })),
							onError: toastError,
						})
					}
				>
					{t.reread}
				</DropdownMenuItem>
				<DropdownMenuItem
					disabled={!owner}
					title={owner ? undefined : LOCKED_TEXT["website.manage"]}
					onSelect={() =>
						exclude.mutate(
							{ pageId: page.id, excluded: !left },
							{
								onSuccess: () => toast.success(fill(left ? t.included : t.excluded, { title })),
								onError: toastError,
							},
						)
					}
				>
					{left ? t.include : t.exclude}
				</DropdownMenuItem>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}

/** Every page found on the site, with a filter by what happened to it, a search and row actions. */
export function PagesTable({ kb, poll }: { kb: string; poll: boolean }) {
	const [chip, setChip] = useState<string>("");
	const [query, setQuery] = useState("");
	const [q, setQ] = useState("");
	const [page, setPage] = useState(1);
	const searchId = useId();
	useEffect(() => {
		const id = setTimeout(() => {
			setQ(query.trim());
			setPage(1);
		}, 250);
		return () => clearTimeout(id);
	}, [query]);
	const pages = useQuery(websitePagesQuery(kb, { status: chip, q, page }, poll));
	const counts = pages.data?.counts ?? {};
	const countOf = (statuses: readonly string[]) => statuses.reduce((n, s) => n + (counts[s] ?? 0), 0);
	const all = Object.values(counts).reduce((n, v) => n + v, 0);

	const columns: Column<WebsitePage>[] = [
		{
			id: "page",
			header: t.colPage,
			cell: (p) => (
				<div className="grid min-w-0 max-w-[460px]">
					<a
						href={p.url}
						target="_blank"
						rel="noopener noreferrer"
						aria-label={fill(t.open, { title: pageTitle(p) })}
						className="inline-flex items-center gap-1 truncate font-semibold text-foreground hover:underline"
					>
						<span className="truncate">{pageTitle(p)}</span>
						<ExternalLink className="size-3 shrink-0 text-muted-foreground" aria-hidden />
					</a>
					<small className="truncate font-mono text-muted-foreground">{pagePath(p.url)}</small>
				</div>
			),
		},
		{
			id: "status",
			header: t.colStatus,
			cell: (p) => {
				const reason = skipReasonWord(p);
				return (
					<div className="grid gap-0.5">
						<Badge variant={pageTone(p.status)} className="w-fit">
							{pageStatusWord(p.status)}
						</Badge>
						{reason ? <small className="text-muted-foreground">{reason}</small> : null}
					</div>
				);
			},
			stackLabel: true,
		},
		{
			id: "checked",
			header: t.colChecked,
			cell: (p) => (p.last_checked_at ? formatRelative(p.last_checked_at) : t.never),
			stackLabel: true,
		},
		// TV5: when the page's text last changed (what the assistant answers from)
		{
			id: "changed",
			header: t.colChanged,
			cell: (p) => formatShortDate(p.last_changed_at),
			stackLabel: true,
		},
		// /TV5
		{
			id: "actions",
			header: t.colActions,
			align: "right",
			className: "w-12",
			cell: (p) => <RowMenu kb={kb} page={p} />,
		},
	];

	const total = pages.data?.total ?? 0;
	return (
		<Panel title={t.title} sub={t.sub}>
			<div className="grid gap-3 p-4">
				<fieldset className="m-0 flex flex-wrap items-center gap-2 border-0 p-0">
					<legend className="sr-only">{t.filtersLabel}</legend>
					<Button
						size="sm"
						variant={chip === "" ? "default" : "outline"}
						aria-pressed={chip === ""}
						onClick={() => {
							setChip("");
							setPage(1);
						}}
					>
						{t.all} ({formatNumber(all)})
					</Button>
					{CHIPS.filter((c) => countOf(c.statuses) > 0 || chip === c.key).map((c) => (
						<Button
							key={c.key}
							size="sm"
							variant={chip === c.key ? "default" : "outline"}
							aria-pressed={chip === c.key}
							onClick={() => {
								setChip(c.key);
								setPage(1);
							}}
						>
							{c.label} ({formatNumber(countOf(c.statuses))})
						</Button>
					))}
				</fieldset>
				<div>
					<label htmlFor={searchId} className="sr-only">
						{t.search}
					</label>
					<Input
						id={searchId}
						type="search"
						value={query}
						placeholder={t.searchPlaceholder}
						onChange={(e) => setQuery(e.target.value)}
					/>
				</div>
			</div>
			{pages.isPending ? (
				<TableSkeleton rows={4} />
			) : pages.isError ? (
				<div className="p-4">
					<ErrorState error={pages.error} title={websiteStrings.loadError} onRetry={() => void pages.refetch()} />
				</div>
			) : (
				<>
					<DataTable
						caption={t.caption}
						columns={columns}
						rows={pages.data.items}
						rowKey={(p) => p.id}
						empty={chip || q ? t.emptyFiltered : t.empty}
					/>
					{total > PAGE_SIZE ? <Pager page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} /> : null}
				</>
			)}
		</Panel>
	);
}
