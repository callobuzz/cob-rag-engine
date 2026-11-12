import { Button } from "@/components/ui/button";
import { formatNumber } from "@/lib/format/number";

/** Offset paging for lists with `total` (README: `?page=` in the URL, 1-based). */
export function Pager({
	page,
	pageSize,
	total,
	onPage,
}: {
	page: number;
	pageSize: number;
	total: number;
	onPage: (page: number) => void;
}) {
	const pages = Math.max(1, Math.ceil(total / pageSize));
	const from = total === 0 ? 0 : (page - 1) * pageSize + 1;
	const to = Math.min(total, page * pageSize);
	return (
		<nav
			aria-label="Pages"
			className="flex items-center justify-between gap-3 px-4 py-3 text-[13px] text-muted-foreground"
		>
			<span>
				{formatNumber(from)}–{formatNumber(to)} of {formatNumber(total)}
			</span>
			<div className="flex gap-2">
				<Button size="sm" disabled={page <= 1} onClick={() => onPage(page - 1)}>
					Previous
				</Button>
				<Button size="sm" disabled={page >= pages} onClick={() => onPage(page + 1)}>
					Next
				</Button>
			</div>
		</nav>
	);
}
