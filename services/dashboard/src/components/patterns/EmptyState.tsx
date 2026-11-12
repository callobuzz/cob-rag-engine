import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Mockup `.empty`: text + next action on the left, "art" (or ways cards) on the right. */
export function EmptyState({
	title,
	body,
	primaryAction,
	secondaryAction,
	art,
	className,
}: {
	title: string;
	body?: ReactNode;
	primaryAction?: ReactNode;
	secondaryAction?: ReactNode;
	art?: ReactNode;
	className?: string;
}) {
	return (
		<div
			className={cn(
				"grid items-center gap-8 rounded-lg border border-border bg-card p-8 min-[861px]:grid-cols-2",
				className,
			)}
		>
			<div>
				<h2 className="text-[22px] font-bold">{title}</h2>
				{body ? <p className="mt-2 mb-[18px] max-w-[460px] text-foreground-2">{body}</p> : null}
				{primaryAction || secondaryAction ? (
					<div className="flex flex-wrap gap-2">
						{primaryAction}
						{secondaryAction}
					</div>
				) : null}
			</div>
			<div aria-hidden={art ? undefined : true}>
				{art ?? (
					<div className="grid gap-2.5 rounded-lg border border-dashed border-input bg-muted p-6">
						<div className="h-3 w-3/4 rounded-md bg-secondary" />
						<div className="h-3 w-1/2 rounded-md bg-secondary" />
						<div className="h-3 w-2/3 rounded-md bg-secondary" />
					</div>
				)}
			</div>
		</div>
	);
}
