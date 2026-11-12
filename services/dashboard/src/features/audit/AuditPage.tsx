import { useQuery } from "@tanstack/react-query";
import { getRouteApi, useNavigate } from "@tanstack/react-router";
import { ErrorState } from "@/components/patterns/ErrorState";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { toApiError } from "@/lib/api/errors";
import { auditStrings as t } from "@/lib/strings/admin";
import { AuditFilters } from "./AuditFilters";
import { AuditTable } from "./AuditTable";
import { auditQuery } from "./api";
import { AUDIT_PAGE_SIZE, type AuditSearch } from "./search";

const route = getRouteApi("/_app/admin/audit");

/** `/admin/audit` (A8 §1): the operator's audit trail with URL filters and paging by 50. */
export function AuditPage() {
	const search = route.useSearch();
	const navigate = useNavigate();
	const q = useQuery(auditQuery(search));
	const page = search.page ?? 1;
	const err = q.isError ? toApiError(q.error) : null;
	// A 422 is about the filters (unknown action, range too long): shown with them.
	const filterError = err?.status === 422 ? err.detail || err.fieldErrors[0]?.message || null : null;
	const filtered = !!(search.action || search.tenant_id || search.actor || search.from || search.to);

	const setSearch = (next: Partial<AuditSearch>) =>
		void navigate({ to: "/admin/audit", search: { ...search, page: undefined, ...next }, replace: true });

	return (
		<div className="grid gap-4">
			<PageHeader title={t.page.title} description={t.page.lead} />
			<AuditFilters search={search} onChange={setSearch} serverError={filterError} />
			{!search.from && !search.to ? <p className="m-0 text-[12.5px] text-muted-foreground">{t.page.range90}</p> : null}
			<div className="overflow-x-auto rounded-lg border border-border bg-card" aria-busy={q.isFetching || undefined}>
				{q.isPending ? (
					<TableSkeleton rows={6} />
				) : err && !filterError ? (
					<div className="p-4">
						<ErrorState error={q.error} title={t.page.error} onRetry={() => void q.refetch()} />
					</div>
				) : (
					<>
						<AuditTable rows={q.data?.items ?? []} empty={filtered ? t.page.empty : t.page.emptyAll} />
						{q.data && q.data.total > AUDIT_PAGE_SIZE ? (
							<Pager
								page={page}
								pageSize={AUDIT_PAGE_SIZE}
								total={q.data.total}
								onPage={(p) => setSearch({ page: p > 1 ? p : undefined })}
							/>
						) : null}
					</>
				)}
			</div>
		</div>
	);
}
