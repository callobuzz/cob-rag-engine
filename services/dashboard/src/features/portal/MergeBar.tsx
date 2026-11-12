import { Combine, X } from "lucide-react";
import { useId, useState } from "react";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";

const m = t.merge;
export const SEPARATORS = { paragraph: "\n\n", line: "\n", space: " " } as const;
export type SeparatorKey = keyof typeof SEPARATORS;

/**
 * Shown once a chunk is selected: how many, the separator, and "Merge N chunks" (enabled from 2,
 * the engine's minimum). The chunks are merged in their workspace order.
 */
export function MergeBar({
	count,
	pending,
	onMerge,
	onClear,
}: {
	count: number;
	pending: boolean;
	onMerge: (separator: string) => void;
	onClear: () => void;
}) {
	const [sep, setSep] = useState<SeparatorKey>("paragraph");
	const id = useId();
	if (count === 0) return null;
	return (
		<section
			aria-label={m.label}
			data-testid="merge-bar"
			className="sticky top-2 z-10 flex flex-wrap items-center gap-3 rounded-lg border border-primary bg-card px-4 py-3 shadow-sm"
		>
			<b className="text-[13.5px]">{fill(m.selected, { n: count })}</b>
			<div className="flex items-center gap-2">
				<label htmlFor={id} className="text-[13px] font-semibold">
					{m.separator}
				</label>
				<NativeSelect
					id={id}
					className="w-[160px]"
					value={sep}
					onChange={(e) => setSep(e.target.value as SeparatorKey)}
				>
					{(Object.keys(SEPARATORS) as SeparatorKey[]).map((k) => (
						<option key={k} value={k}>
							{m.separators[k]}
						</option>
					))}
				</NativeSelect>
			</div>
			{count < 2 ? <span className="text-[12.5px] text-muted-foreground">{m.needTwo}</span> : null}
			<span className="ml-auto flex gap-2">
				<Button size="sm" variant="ghost" onClick={onClear}>
					<X aria-hidden />
					{m.clear}
				</Button>
				<Button size="sm" variant="default" disabled={count < 2 || pending} onClick={() => onMerge(SEPARATORS[sep])}>
					<Combine aria-hidden />
					{fill(m.merge, { n: count })}
				</Button>
			</span>
		</section>
	);
}
