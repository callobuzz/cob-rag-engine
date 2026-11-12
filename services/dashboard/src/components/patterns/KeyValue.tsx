import type { ReactNode } from "react";

/** Mockup `.kv`: mono label column, value column; stacks under 861 px. */
export function KeyValue({ rows }: { rows: { label: string; value: ReactNode; muted?: boolean }[] }) {
	return (
		<dl className="m-0 grid">
			{rows.map((r) => (
				<div
					key={r.label}
					className="grid gap-0.5 border-b border-border px-4 py-2.5 text-[13px] last:border-b-0 min-[861px]:grid-cols-[170px_minmax(0,1fr)] min-[861px]:gap-3"
				>
					<dt className="font-mono text-[11.5px] text-muted-foreground">{r.label}</dt>
					<dd className={`m-0 font-mono font-medium break-all ${r.muted ? "text-muted-foreground" : ""}`}>{r.value}</dd>
				</div>
			))}
		</dl>
	);
}
