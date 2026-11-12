import { useQuery } from "@tanstack/react-query";
import { getRouteApi } from "@tanstack/react-router";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { Segmented } from "@/components/patterns/Segmented";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import type { Corpus } from "@/lib/api/queries";
import { reviewStrings as t } from "@/lib/strings/review";
import { reviewQuery } from "./api";
import { ReviewItemCard } from "./ReviewItemCard";
import { REVIEW_KINDS, type ReviewKindFilter } from "./search";

const route = getRouteApi("/_app/review");

/** `/review`: what the assistant couldn't answer well, grouped, each with the fixes that help. */
export function ReviewPage() {
	return (
		<KbScopedPage title={t.title} emptyBody={t.lead}>
			{(kb) => <ReviewBody kb={kb} />}
		</KbScopedPage>
	);
}

function ReviewBody({ kb }: { kb: Corpus }) {
	const search = route.useSearch();
	const navigate = route.useNavigate();
	const q = useQuery(reviewQuery(kb.id, { kind: search.kind }));
	return (
		<>
			<PageHeader
				title={t.title}
				description={t.lead}
				actions={
					<Segmented<ReviewKindFilter | "all">
						label={t.filter}
						value={search.kind ?? "all"}
						options={[
							{ value: "all", label: t.kinds.all },
							...REVIEW_KINDS.map((k) => ({ value: k, label: t.kinds[k] })),
						]}
						onChange={(v) => void navigate({ search: (prev) => ({ ...prev, kind: v === "all" ? undefined : v }) })}
					/>
				}
			/>
			{q.isPending ? (
				<CardSkeleton lines={4} />
			) : q.isError ? (
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			) : q.data.items.length === 0 ? (
				<EmptyState title={t.emptyTitle} body={t.emptyBody} />
			) : (
				<ul aria-label={t.listLabel} className="m-0 grid list-none gap-3 p-0">
					{q.data.items.map((item) => (
						<li key={item.item_key}>
							<ReviewItemCard kb={kb.id} item={item} />
						</li>
					))}
				</ul>
			)}
		</>
	);
}
