import { ChevronRight } from "lucide-react";
import { docsStrings as t } from "@/lib/strings/docs";
import { cn } from "@/lib/utils";
import type { DocsTocItem } from "./api";

/**
 * "On this page": the sections of the page as in-page links. A wide screen shows it as a column
 * beside the article; a narrow one as a disclosure at the top.
 */
export function DocsToc({
	items,
	activeAnchor,
	onJump,
	variant,
}: {
	items: DocsTocItem[];
	activeAnchor: string;
	onJump: (anchor: string) => void;
	variant: "column" | "disclosure";
}) {
	if (items.length === 0) return null;
	const list = (
		<ul className="m-0 grid list-none gap-0.5 p-0 text-[13px]">
			{items.map((i) => (
				<li key={i.anchor}>
					<a
						href={`#${i.anchor}`}
						onClick={(e) => {
							e.preventDefault();
							onJump(i.anchor);
						}}
						aria-current={i.anchor === activeAnchor ? "location" : undefined}
						className={cn(
							"block rounded-sm border-l-2 py-1 pr-2 no-underline hover:text-foreground",
							i.level > 2 ? "pl-5" : "pl-3",
							i.anchor === activeAnchor
								? "border-primary font-semibold text-foreground"
								: "border-transparent text-muted-foreground",
						)}
					>
						{i.title}
					</a>
				</li>
			))}
		</ul>
	);
	if (variant === "disclosure") {
		return (
			<details className="group rounded-md border border-border bg-card px-3 py-2 min-[1200px]:hidden">
				<summary className="flex cursor-pointer list-none items-center gap-1.5 text-[13px] font-semibold [&::-webkit-details-marker]:hidden">
					<ChevronRight className="size-3.5 transition-transform group-open:rotate-90" aria-hidden />
					{t.tocToggle}
				</summary>
				<nav aria-label={t.tocLabel} className="pt-2">
					{list}
				</nav>
			</details>
		);
	}
	return (
		<nav aria-label={t.tocLabel} className="sticky top-4 hidden self-start min-[1200px]:block">
			<div className="nav-label pb-1.5">{t.tocLabel}</div>
			{list}
		</nav>
	);
}
