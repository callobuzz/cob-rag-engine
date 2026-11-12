import { Link } from "@tanstack/react-router";
import { ChevronRight } from "lucide-react";
import { Fragment, type ReactNode } from "react";
import { HelpLink } from "@/features/docs/HelpLink";
import { useDocumentTitle } from "@/lib/branding/useDocumentTitle";

/** Mockup `.page-head`: crumbs, title (h1, focus target after navigation), description, actions. */
export function PageHeader({
	title,
	description,
	crumbs,
	actions,
	leading,
}: {
	title: string;
	description?: ReactNode;
	crumbs?: { label: string; to?: string; search?: Record<string, string | undefined> }[];
	actions?: ReactNode;
	/** Shown left of the title (e.g. a knowledge base avatar). */
	leading?: ReactNode;
}) {
	useDocumentTitle(title);
	return (
		<div className="flex flex-wrap items-end justify-between gap-4">
			<div className="min-w-0">
				{crumbs?.length ? (
					<nav aria-label="Breadcrumb" className="mb-1.5 flex items-center gap-1.5 text-[12.5px] text-muted-foreground">
						{crumbs.map((c, i) => (
							<Fragment key={c.label}>
								{i > 0 ? <ChevronRight className="size-3" aria-hidden /> : null}
								{c.to ? (
									<Link
										to={c.to}
										search={c.search}
										className="text-muted-foreground no-underline hover:text-foreground"
									>
										{c.label}
									</Link>
								) : (
									<span>{c.label}</span>
								)}
							</Fragment>
						))}
					</nav>
				) : null}
				<div className="flex min-w-0 items-center gap-3.5">
					{leading}
					<div className="min-w-0">
						<h1 className="text-2xl font-bold break-words" tabIndex={-1}>
							{title}
						</h1>
						{description ? <p className="mt-1 mb-0 text-foreground-2">{description}</p> : null}
					</div>
					<HelpLink />
				</div>
			</div>
			{actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
		</div>
	);
}
