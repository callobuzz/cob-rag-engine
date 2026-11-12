import { Link, type LinkProps } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Mockup `.list`: rows separated by hairlines. */
export function List({ children, label }: { children: ReactNode; label?: string }) {
	return (
		<ul aria-label={label} className="m-0 grid list-none p-0">
			{children}
		</ul>
	);
}

/** Mockup `.list .row`: optional leading slot, a growing text column, a trailing slot. */
export function Row({
	lead,
	title,
	sub,
	trail,
	wrap,
	children,
	className,
	testId,
}: {
	lead?: ReactNode;
	title: ReactNode;
	sub?: ReactNode;
	trail?: ReactNode;
	wrap?: boolean;
	children?: ReactNode;
	className?: string;
	testId?: string;
}) {
	return (
		<li
			data-testid={testId}
			className={cn("flex min-w-0 items-center gap-3 border-b border-border px-4 py-[11px] last:border-b-0", className)}
		>
			{lead}
			<div className="min-w-0 flex-1">
				<div className={cn("font-semibold", wrap ? "break-words" : "truncate")}>{title}</div>
				{sub ? <div className="text-[12.5px] text-muted-foreground">{sub}</div> : null}
				{children}
			</div>
			{trail}
		</li>
	);
}

/** Mockup `.btn-sm.btn-ghost` "All" link in a card head. */
export function HeadLink({ label, children, ...link }: LinkProps & { label: string; children: ReactNode }) {
	return (
		<Link {...link} aria-label={label} className={buttonVariants({ variant: "ghost", size: "sm" })}>
			{children}
		</Link>
	);
}

/** A quiet one-line note inside a card (empty lists). */
export function Muted({ children }: { children: ReactNode }) {
	return <p className="m-0 px-4 py-3.5 text-[13px] text-muted-foreground">{children}</p>;
}
