import { playgroundStrings as t } from "@/lib/strings/playground";
import type { StreamSource } from "./api";
import { sourceName } from "./SourcesPanel";

/**
 * Mockup `.cites`: citation chips `[n] file · section`. Each is a button that highlights the
 * matching passage in Sources and moves focus to it.
 */
export function Citations({ sources, onPick }: { sources: StreamSource[]; onPick: (index: number) => void }) {
	if (!sources.length) return null;
	return (
		<ul aria-label={t.chat.citationsLabel} className="m-0 mt-2 flex list-none flex-wrap gap-1.5 p-0">
			{sources.map((s) => {
				const name = sourceName(s);
				return (
					<li key={`${s.chunk_id}-${s.index}`} className="min-w-0 max-w-full">
						<button
							type="button"
							onClick={() => onPick(s.index)}
							className="inline-flex h-6 max-w-full min-w-0 items-center gap-1.5 rounded-[6px] border border-input bg-card px-2 text-xs text-foreground-2 hover:bg-accent focus-visible:shadow-[0_0_0_2px_var(--ring)] focus-visible:outline-none"
						>
							<b className="font-mono font-medium text-primary">[{s.index}]</b>{" "}
							<span className="truncate">
								{name}
								{s.section ? ` · ${s.section}` : ""}
							</span>
						</button>
					</li>
				);
			})}
		</ul>
	);
}
