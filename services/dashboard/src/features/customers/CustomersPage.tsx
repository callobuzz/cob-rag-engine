import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { CircleCheck, Pause, Plus, Search } from "lucide-react";
import { type ReactNode, useEffect, useId, useRef, useState } from "react";
import { FormAlert } from "@/components/auth/FormAlert";
import { DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useDebouncedValue } from "@/features/documents/ui";
import { fairUseListQuery } from "@/features/fair-use/api"; // PIP4
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatNumber } from "@/lib/format/number";
import { customersStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { AddCustomerDialog } from "./AddCustomerDialog";
import { CUSTOMERS_PAGE_SIZE, type TenantStatusTab, tenantCountQuery, tenantsQuery } from "./api";
import { customerColumns } from "./CustomerRow";
import { useCustomerActions } from "./LifecycleDialogs";
import { OperatorWorkspaceCard } from "./OperatorWorkspaceCard";

const route = getRouteApi("/_app/admin/customers/");
const TABS: TenantStatusTab[] = ["pending", "active", "suspended"];

/** `/admin/customers` (mockup `[data-view=customers]`). */
export function CustomersPage() {
	const { status: statusParam, q = "", page = 1, add } = route.useSearch();
	const navigate = useNavigate();
	const { productName, signupMode } = useBranding();
	const [adding, setAdding] = useState(false);
	const search = q.trim() || undefined;
	// `?add=1` (the empty-state links, the getting-started list) opens the dialog, then leaves the URL clean.
	useEffect(() => {
		if (!add) return;
		setAdding(true);
		void navigate({
			to: "/admin/customers",
			search: { status: statusParam, q: q || undefined, page },
			replace: true,
		});
	}, [add, navigate, statusParam, q, page]);

	const counts = {
		pending: useQuery(tenantCountQuery("pending", search)),
		active: useQuery(tenantCountQuery("active", search)),
		suspended: useQuery(tenantCountQuery("suspended", search)),
	};
	// Default tab: pending when anyone is waiting, else active (D7 decision).
	const status: TenantStatusTab | null =
		statusParam ?? (counts.pending.isPending ? null : (counts.pending.data ?? 0) > 0 ? "pending" : "active");

	const setSearch = (next: { status?: TenantStatusTab; q?: string; page?: number }) =>
		void navigate({ to: "/admin/customers", search: { status: statusParam, q: q || undefined, page, ...next } });
	const actions = useCustomerActions();

	return (
		<>
			<PageHeader
				title={t.list.title}
				description={fill(t.list.lead, { product: productName })}
				actions={
					<>
						<Link
							to="/admin/branding"
							className="inline-flex h-9 items-center gap-2 rounded-full border border-border bg-card px-3 text-[13px] whitespace-nowrap text-foreground-2 no-underline hover:bg-accent"
						>
							<span aria-hidden className="text-warning">
								●
							</span>
							{t.list.modeChip} <b className="text-foreground">{t.list.modes[signupMode]}</b>
						</Link>
						<Button variant="default" onClick={() => setAdding(true)}>
							<Plus aria-hidden />
							{t.list.add}
						</Button>
					</>
				}
			/>
			{search ? null : <OperatorWorkspaceCard />}
			<SearchBox value={q} onChange={(v) => setSearch({ q: v || undefined, page: undefined })} />
			<Tabs
				value={status ?? ""}
				onValueChange={(v) => setSearch({ status: v as TenantStatusTab, page: undefined })}
				className="min-w-0 gap-4"
			>
				<TabsList
					variant="line"
					aria-label={t.list.tabsLabel}
					className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0 [scrollbar-width:none]"
				>
					{TABS.map((s) => {
						const n = counts[s].data;
						return (
							<TabsTrigger
								key={s}
								value={s}
								aria-label={n == null ? t.list.tabs[s] : fill(t.list.tabCount, { label: t.list.tabs[s], count: n })}
								className={cn(
									"h-10! flex-none gap-1.5 rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
									"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
								)}
							>
								{t.list.tabs[s]}
								{n == null ? null : (
									<Badge variant={s === "pending" && n > 0 ? "warn" : "default"} aria-hidden>
										{formatNumber(n)}
									</Badge>
								)}
							</TabsTrigger>
						);
					})}
				</TabsList>
				{TABS.map((s) => (
					<TabsContent key={s} value={s} className="min-w-0">
						{status === s ? (
							<StatusPanel
								status={s}
								search={search}
								page={page}
								onPage={(p) => setSearch({ page: p })}
								run={actions.run}
							/>
						) : null}
					</TabsContent>
				))}
			</Tabs>
			{status ? null : (
				<div className="rounded-lg border border-border bg-card">
					<TableSkeleton rows={3} />
				</div>
			)}
			{actions.dialogs}
			<AddCustomerDialog open={adding} onOpenChange={setAdding} />
		</>
	);
}

function StatusPanel({
	status,
	search,
	page,
	onPage,
	run,
}: {
	status: TenantStatusTab;
	search?: string;
	page: number;
	onPage: (p: number) => void;
	run: ReturnType<typeof useCustomerActions>["run"];
}) {
	const navigate = useNavigate();
	const list = useQuery(
		tenantsQuery({ status, search, offset: (page - 1) * CUSTOMERS_PAGE_SIZE, limit: CUSTOMERS_PAGE_SIZE }),
	);
	const rows = list.data?.items ?? [];
	const total = list.data?.total ?? 0;
	// PIP4: who is near a limit, slowed down or paused (a failure here only hides the column)
	// Asked for exactly the rows shown, so a business is never given a state it was not looked up for.
	const shownIds = rows.map((x) => x.id);
	const fair = useQuery({
		...fairUseListQuery({ ids: shownIds, limit: Math.max(1, shownIds.length) }),
		enabled: status === "active" && shownIds.length > 0,
	});
	const fairStates = fair.data
		? new Map(fair.data.items.map((r) => [r.tenant_id, { state: r.state, exempt: r.exempt ?? false }]))
		: undefined;
	// /PIP4
	const emptyText = search
		? fill(t.list.searchEmpty, { q: search })
		: status === "pending"
			? t.list.pendingEmpty
			: status === "active"
				? t.list.activeEmpty
				: t.list.suspendedEmpty;

	let body: ReactNode;
	if (list.isPending) body = <TableSkeleton rows={3} />;
	else if (list.isError)
		body = (
			<div className="p-4">
				<ErrorState error={list.error} onRetry={() => void list.refetch()} />
			</div>
		);
	else if (rows.length === 0)
		body = (
			<NoteRow icon={status === "suspended" ? "pause" : "check"} testId="customers-empty">
				{emptyText}
			</NoteRow>
		);
	else
		body = (
			<>
				<DataTable
					caption={t.list.captions[status]}
					columns={customerColumns(status, run, fairStates)}
					rows={rows}
					rowKey={(x) => x.id}
					onRowClick={(x) => void navigate({ to: "/admin/customers/$tenantId", params: { tenantId: x.id } })}
				/>
				{total > CUSTOMERS_PAGE_SIZE ? (
					<Pager page={page} pageSize={CUSTOMERS_PAGE_SIZE} total={total} onPage={onPage} />
				) : null}
				{status === "suspended" ? <NoteRow icon="pause">{t.list.suspendedNote}</NoteRow> : null}
			</>
		);

	return (
		<div className="grid gap-4" data-testid={`customers-${status}`}>
			<div className="overflow-x-auto rounded-lg border border-border bg-card">{body}</div>
			{status === "pending" ? (
				<FormAlert tone="info">
					<b className="font-semibold">{t.list.billingTitle}</b> {t.list.billingBody}
				</FormAlert>
			) : null}
		</div>
	);
}

export function NoteRow({ icon, children, testId }: { icon: "check" | "pause"; children: ReactNode; testId?: string }) {
	const Icon = icon === "check" ? CircleCheck : Pause;
	return (
		<div
			data-testid={testId}
			className="flex items-start gap-2.5 border-t border-border bg-secondary px-4 py-3 text-[13px] text-foreground-2 first:border-t-0 last:rounded-b-lg"
		>
			<Icon className="mt-px size-4 shrink-0 text-muted-foreground" aria-hidden />
			<span>{children}</span>
		</div>
	);
}

/** Debounced (300 ms) search box; the URL's `?q=` is the source of truth. */
function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
	const id = useId();
	const [text, setText] = useState(value);
	const debounced = useDebouncedValue(text, 300);
	const latest = useRef({ value, onChange });
	latest.current = { value, onChange };
	useEffect(() => {
		const next = debounced.trim();
		if (next !== latest.current.value) latest.current.onChange(next);
	}, [debounced]);
	return (
		<div className="relative max-w-[420px]">
			<label htmlFor={id} className="sr-only">
				{t.list.search}
			</label>
			<Search
				className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-muted-foreground"
				aria-hidden
			/>
			<Input
				id={id}
				type="search"
				className="pl-9"
				placeholder={t.list.searchPlaceholder}
				value={text}
				maxLength={255}
				onChange={(e) => setText(e.target.value)}
			/>
		</div>
	);
}
