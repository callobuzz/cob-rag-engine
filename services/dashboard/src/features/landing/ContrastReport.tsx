import { Check } from "lucide-react";
import { landingStrings as t } from "@/lib/strings/landing";
import type { LandingThemePreview } from "./api";

const SWATCHES = ["bg", "surface", "ink", "brand", "brand-text", "accent"] as const;

function Swatches({ label, colours }: { label: string; colours: Record<string, string> }) {
	return (
		<div className="grid gap-1.5">
			<span className="text-[12.5px] font-semibold text-foreground-2">{label}</span>
			<ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
				{SWATCHES.filter((k) => colours[k]).map((k) => (
					<li key={k} className="grid justify-items-center gap-0.5 text-[11px] text-muted-foreground">
						<span
							role="img"
							aria-label={`${k} ${colours[k]}`}
							className="size-7 rounded-sm border border-border"
							style={{ background: colours[k] }}
						/>
						{k}
					</li>
				))}
			</ul>
		</div>
	);
}

/**
 * The palette a colour makes, in light and dark, and every text pair with how readable it is. The
 * numbers come from the engine (one place does the colour maths); every pair it lists passes, and
 * where it had to darken or lighten your colour it says so in a sentence.
 */
export function ContrastReport({ theme, pending }: { theme: LandingThemePreview; pending?: boolean }) {
	return (
		<div className="grid gap-4" data-testid="contrast-report" aria-busy={pending}>
			<div className="grid gap-4 min-[700px]:grid-cols-2">
				<Swatches label={t.look.light} colours={theme.light} />
				<Swatches label={t.look.dark} colours={theme.dark} />
			</div>
			{theme.adjusted.length > 0 ? (
				<ul
					className="m-0 grid list-none gap-1 rounded-sm bg-secondary p-3 text-[13px]"
					data-testid="contrast-adjusted"
				>
					{theme.adjusted.map((s) => (
						<li key={s}>{s}</li>
					))}
				</ul>
			) : null}
			<details>
				<summary className="cursor-pointer text-[13px] font-semibold">{t.look.reportTitle}</summary>
				<table className="mt-2 w-full border-collapse text-left text-[12.5px]">
					<thead>
						<tr className="text-muted-foreground">
							<th className="py-1 pr-2 font-medium">{t.look.pair}</th>
							<th className="py-1 pr-2 font-medium">{t.look.mode}</th>
							<th className="py-1 pr-2 font-medium">{t.look.readability}</th>
						</tr>
					</thead>
					<tbody>
						{theme.report.map((row) => (
							<tr key={`${row.theme}-${row.pair}`} className="border-t border-border" data-testid="contrast-row">
								<td className="py-1 pr-2">{row.pair}</td>
								<td className="py-1 pr-2">{row.theme === "dark" ? t.look.dark : t.look.light}</td>
								<td className="py-1 pr-2 tabular-nums">
									{row.ratio.toFixed(1)}:1 <Check className="inline size-3.5 text-success" aria-label={t.look.passes} />
								</td>
							</tr>
						))}
					</tbody>
				</table>
			</details>
		</div>
	);
}
