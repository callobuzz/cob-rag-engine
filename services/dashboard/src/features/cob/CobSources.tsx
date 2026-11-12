import { ExternalLink } from "lucide-react";
import { cobStrings as t } from "@/lib/strings/cob";
import { fill } from "@/lib/strings/en";
import { sourceTarget, useCobLinks } from "./links";
import type { CobSource } from "./types";

const CHIP =
	"inline-flex max-w-full items-center gap-1.5 rounded-full border border-border bg-card px-2.5 py-1 text-[12px] text-foreground-2 no-underline hover:bg-accent hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:outline-none";

/** The pages COB used, as chips under the answer. The number matches the [n] in the text. */
export function CobSources({ sources }: { sources: CobSource[] }) {
	const { hasDocs, onInternalClick } = useCobLinks();
	if (!sources.length) return null;
	return (
		<ul aria-label={t.sources.label} className="m-0 flex list-none flex-wrap gap-1.5 p-0" data-testid="cob-sources">
			{sources.map((s) => {
				const target = sourceTarget(s, hasDocs);
				const label = (
					<>
						<span className="font-semibold text-foreground">{s.index}</span>
						<span className="truncate">{s.title}</span>
					</>
				);
				return (
					<li key={`${s.index}-${s.slug}-${s.anchor}`} className="min-w-0 max-w-full">
						{target?.kind === "internal" ? (
							<a
								href={target.href}
								data-source={s.index}
								aria-label={fill(t.sources.open, { title: s.title })}
								onClick={onInternalClick(target.href)}
								className={CHIP}
							>
								{label}
							</a>
						) : target?.kind === "external" ? (
							<a
								href={target.url}
								target="_blank"
								rel="noopener noreferrer"
								data-source={s.index}
								aria-label={fill(t.sources.openPublic, { title: s.title })}
								className={CHIP}
							>
								{label}
								<ExternalLink className="size-3 shrink-0" aria-hidden />
							</a>
						) : (
							<span data-source={s.index} tabIndex={-1} className={CHIP}>
								{label}
							</span>
						)}
					</li>
				);
			})}
		</ul>
	);
}
