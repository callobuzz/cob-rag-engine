import { type ReactNode, useEffect, useState } from "react";
import { Skeleton } from "@/components/ui/skeleton";

/** Renders children only after `ms` (150 by default) so fast loads don't flash a skeleton. */
export function Delayed({ ms = 150, children }: { ms?: number; children: ReactNode }) {
	const [show, setShow] = useState(ms <= 0);
	useEffect(() => {
		if (ms <= 0) return;
		const t = setTimeout(() => setShow(true), ms);
		return () => clearTimeout(t);
	}, [ms]);
	return show ? children : null;
}

export function CardSkeleton({ lines = 3 }: { lines?: number }) {
	return (
		<Delayed>
			<div className="grid gap-3 rounded-lg border border-border bg-card p-4" aria-hidden>
				<Skeleton className="h-4 w-1/3" />
				{Array.from({ length: lines }, (_, i) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: static placeholder rows
					<Skeleton key={i} className="h-3 w-full" />
				))}
			</div>
		</Delayed>
	);
}

export function TableSkeleton({ rows = 5 }: { rows?: number }) {
	return (
		<Delayed>
			<div className="grid gap-2 p-4" aria-hidden>
				{Array.from({ length: rows }, (_, i) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: static placeholder rows
					<Skeleton key={i} className="h-8 w-full" />
				))}
			</div>
		</Delayed>
	);
}
