import { CopyButton } from "@/components/patterns/CopyButton";
import { fill } from "@/lib/strings/en";
import { widgetStrings as t } from "@/lib/strings/widget";

/** The key's `embed_snippet` (from the engine), a copy button and the widget's data-* attributes. */
export function SnippetBox({ name, snippet }: { name: string; snippet: string }) {
	return (
		<div className="grid gap-2">
			<div className="flex flex-wrap items-center justify-between gap-2">
				<div className="min-w-0">
					<div className="text-[13px] font-semibold">{t.snippet.title}</div>
					<div className="text-[12.5px] text-muted-foreground">{t.snippet.hint}</div>
				</div>
				<CopyButton value={snippet} label={t.snippet.copy} ariaLabel={fill(t.snippet.copyFor, { name })} />
			</div>
			<figure aria-label={fill(t.snippet.label, { name })} className="m-0 min-w-0">
				<pre
					// biome-ignore lint/a11y/noNoninteractiveTabindex: scrollable code must be reachable by keyboard
					tabIndex={0}
					data-testid="snippet"
					className="m-0 overflow-x-auto rounded-md border border-border bg-muted p-3 font-mono text-[12.5px] leading-relaxed whitespace-pre-wrap break-all"
				>
					{snippet}
				</pre>
			</figure>
			<details className="text-[13px]">
				<summary className="cursor-pointer font-semibold text-foreground-2">{t.snippet.devTitle}</summary>
				<p className="mt-2 mb-1.5 text-foreground-2">{t.snippet.devLead}</p>
				<dl className="m-0 grid gap-1.5 min-[600px]:grid-cols-[max-content_1fr] min-[600px]:gap-x-4">
					{t.snippet.attrs.map((a) => (
						<div key={a.a} className="contents">
							<dt className="font-mono text-[12.5px]">{a.a}</dt>
							<dd className="m-0 text-foreground-2">{a.t}</dd>
						</div>
					))}
				</dl>
			</details>
		</div>
	);
}
