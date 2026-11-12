import { useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PRESET_ORDER, presetLabel } from "@/features/kbs/PresetCards";
import { RETRIEVAL_FIELDS } from "@/features/kbs/retrieval-fields";
import { formatNumber } from "@/lib/format/number";
import { modelsStrings } from "@/lib/strings/admin";
import { presetsQuery } from "./api";

const t = modelsStrings.presets;

function settingLabel(key: string): string {
	return RETRIEVAL_FIELDS.find((f) => f.key === key)?.label ?? key.replace(/_/g, " ");
}

function valueText(v: unknown): string {
	if (typeof v === "boolean") return v ? t.on : t.off;
	if (typeof v === "number") return formatNumber(v);
	if (v == null) return "—";
	return String(v);
}

/** `GET /models/presets`: one row per setting, one column per preset, plain labels (D3's). */
export function PresetsTable() {
	const q = useQuery(presetsQuery);
	if (q.isPending) return <TableSkeleton rows={6} />;
	if (q.isError)
		return (
			<div className="p-4">
				<ErrorState error={q.error} onRetry={() => void q.refetch()} />
			</div>
		);
	const presets = q.data.presets;
	const names = [
		...PRESET_ORDER.filter((n) => n in presets),
		...Object.keys(presets).filter((n) => !PRESET_ORDER.includes(n as never)),
	];
	const keys = [...new Set(names.flatMap((n) => Object.keys(presets[n] ?? {})))];
	return (
		<>
			<p className="m-0 px-4 pt-3.5 text-[13px] text-foreground-2">{t.lead}</p>
			<table className="w-full border-collapse">
				<caption className="sr-only">{t.caption}</caption>
				<thead>
					<tr>
						<th
							scope="col"
							className="density-cell border-b border-border text-left font-mono text-[10.5px] font-medium tracking-[.08em] text-muted-foreground uppercase"
						>
							{t.setting}
						</th>
						{names.map((n) => (
							<th
								key={n}
								scope="col"
								className="density-cell border-b border-border text-right font-mono text-[10.5px] font-medium tracking-[.08em] text-muted-foreground uppercase"
							>
								{presetLabel(n)}
							</th>
						))}
					</tr>
				</thead>
				<tbody>
					{keys.map((k) => (
						<tr key={k} className="border-b border-border last:border-b-0">
							<th scope="row" className="density-cell text-left text-[13px] font-normal">
								{settingLabel(k)}
								<code className="ml-1.5 font-mono text-[11px] text-muted-foreground max-[600px]:block max-[600px]:ml-0">
									{k}
								</code>
							</th>
							{names.map((n) => (
								<td key={n} className="density-cell text-right text-[13px] tabular-nums">
									{valueText(presets[n]?.[k])}
								</td>
							))}
						</tr>
					))}
				</tbody>
			</table>
		</>
	);
}
