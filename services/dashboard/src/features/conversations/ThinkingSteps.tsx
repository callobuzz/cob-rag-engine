import { Wrench } from "lucide-react";
import { fill } from "@/lib/strings/en";
import { playgroundStrings } from "@/lib/strings/playground";
import type { ToolStep } from "./api";

const t = playgroundStrings.conversations;

/** A streamed reply's `thinking` steps and tool calls (with duration), as a collapsible list. */
export function ThinkingSteps({ steps, tools }: { steps: string[]; tools: ToolStep[] }) {
	const n = steps.length + tools.length;
	if (!n) return null;
	return (
		<details className="mb-1.5 text-xs text-muted-foreground">
			<summary className="cursor-pointer select-none font-semibold">{fill(t.thinkingCount, { n })}</summary>
			<ol className="m-0 mt-1.5 grid list-none gap-1 p-0" aria-label={t.thinking}>
				{steps.map((s, i) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: steps are an append-only list
					<li key={`s${i}`}>{s}</li>
				))}
				{tools.map((tool, i) => (
					// biome-ignore lint/suspicious/noArrayIndexKey: tool calls are an append-only list
					<li key={`t${i}`} className="flex items-center gap-1.5">
						<Wrench className="size-3" aria-hidden />
						<span className="font-mono">
							{tool.durationMs != null
								? fill(t.toolDuration, { tool: tool.tool, ms: Math.round(tool.durationMs) })
								: fill(t.toolCall, { tool: tool.tool })}
						</span>
						{tool.summary ? <span>· {tool.summary}</span> : null}
					</li>
				))}
			</ol>
		</details>
	);
}
