import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { type ReactNode, useMemo, useState } from "react";
import { cn } from "@/lib/utils";

/**
 * Simple table (no TanStack Table): sortable headers with aria-sort, `stack-sm` card layout under
 * 600 px (mockup), density from <html data-density>.
 */
export interface Column<T> {
	id: string;
	header: string;
	cell: (row: T) => ReactNode;
	sortValue?: (row: T) => string | number;
	align?: "left" | "right";
	className?: string;
	/** Under 600 px (stacked rows) show the header before the value. */
	stackLabel?: boolean;
	/** Render this cell as the row header (`<th scope="row">`). */
	rowHeader?: boolean;
}

export function DataTable<T>({
	columns,
	rows,
	rowKey,
	caption,
	empty,
	initialSort,
	onRowClick,
	footer,
	rowClassName,
}: {
	columns: Column<T>[];
	rows: T[];
	rowKey: (row: T) => string;
	caption?: string;
	empty?: ReactNode;
	initialSort?: { id: string; dir: "asc" | "desc" };
	/** Whole-row click (mouse convenience; keep a real link in a cell for keyboards). */
	onRowClick?: (row: T) => void;
	/** A totals row (`<tfoot>`), one cell per column id; the first column is its row header. */
	footer?: Partial<Record<string, ReactNode>>;
	/** Extra classes for one row (for example to dim it). */
	rowClassName?: (row: T) => string | undefined;
}) {
	const [sort, setSort] = useState(initialSort ?? null);
	const sorted = useMemo(() => {
		if (!sort) return rows;
		const col = columns.find((c) => c.id === sort.id);
		if (!col?.sortValue) return rows;
		const get = col.sortValue;
		return [...rows].sort((a, b) => {
			const va = get(a);
			const vb = get(b);
			const r =
				typeof va === "number" && typeof vb === "number" ? va - vb : String(va).localeCompare(String(vb), "en-IN");
			return sort.dir === "asc" ? r : -r;
		});
	}, [rows, columns, sort]);

	return (
		<table className="stack-sm w-full border-collapse">
			{caption ? <caption className="sr-only">{caption}</caption> : null}
			<thead>
				<tr>
					{columns.map((c) => {
						const active = sort?.id === c.id;
						const ariaSort = active
							? sort?.dir === "asc"
								? "ascending"
								: "descending"
							: c.sortValue
								? "none"
								: undefined;
						return (
							<th
								key={c.id}
								scope="col"
								aria-sort={ariaSort}
								className={cn(
									"density-cell border-b border-border font-mono text-[10.5px] font-medium tracking-[.08em] text-muted-foreground uppercase",
									c.align === "right" ? "text-right" : "text-left",
								)}
							>
								{c.sortValue ? (
									<button
										type="button"
										className="inline-flex items-center gap-1 uppercase"
										onClick={() =>
											setSort((s) =>
												s?.id === c.id ? { id: c.id, dir: s.dir === "asc" ? "desc" : "asc" } : { id: c.id, dir: "asc" },
											)
										}
									>
										{c.header}
										{active ? (
											sort?.dir === "asc" ? (
												<ArrowUp className="size-3" aria-hidden />
											) : (
												<ArrowDown className="size-3" aria-hidden />
											)
										) : (
											<ArrowUpDown className="size-3 opacity-50" aria-hidden />
										)}
									</button>
								) : (
									c.header
								)}
							</th>
						);
					})}
				</tr>
			</thead>
			<tbody>
				{sorted.length === 0 && empty ? (
					<tr>
						<td colSpan={columns.length} className="density-cell text-center text-muted-foreground">
							{empty}
						</td>
					</tr>
				) : (
					sorted.map((row) => (
						<tr
							key={rowKey(row)}
							className={cn(
								"border-b border-border last:border-b-0",
								onRowClick && "cursor-pointer hover:bg-accent",
								rowClassName?.(row),
							)}
							onClick={
								onRowClick
									? (e) => {
											if ((e.target as HTMLElement).closest("a,button,input,select,textarea")) return;
											onRowClick(row);
										}
									: undefined
							}
						>
							{columns.map((c) => {
								const Cell = c.rowHeader ? "th" : "td";
								return (
									<Cell
										key={c.id}
										scope={c.rowHeader ? "row" : undefined}
										className={cn(
											"density-cell text-[13px]",
											c.rowHeader && "font-normal",
											c.align === "right" ? "text-right tabular-nums max-[600px]:text-left" : "text-left",
											c.className,
										)}
									>
										{c.stackLabel ? (
											<span aria-hidden className="mr-1.5 hidden text-muted-foreground max-[600px]:inline">
												{c.header}:
											</span>
										) : null}
										{c.cell(row)}
									</Cell>
								);
							})}
						</tr>
					))
				)}
			</tbody>
			{footer ? (
				<tfoot>
					<tr className="border-t-2 border-border bg-secondary font-semibold">
						{columns.map((c, i) => {
							const Cell = i === 0 ? "th" : "td";
							return (
								<Cell
									key={c.id}
									scope={i === 0 ? "row" : undefined}
									className={cn(
										"density-cell text-[13px]",
										c.align === "right" ? "text-right tabular-nums max-[600px]:text-left" : "text-left",
									)}
								>
									{c.stackLabel && footer[c.id] != null ? (
										<span aria-hidden className="mr-1.5 hidden font-normal text-muted-foreground max-[600px]:inline">
											{c.header}:
										</span>
									) : null}
									{footer[c.id]}
								</Cell>
							);
						})}
					</tr>
				</tfoot>
			) : null}
		</table>
	);
}
