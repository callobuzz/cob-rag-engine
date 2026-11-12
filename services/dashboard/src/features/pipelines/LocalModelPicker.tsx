import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { answerBenchmarksQuery } from "@/features/benchmarks/api";
import { localLlmQuery } from "@/features/engine/local-llm-api";
import { fill } from "@/lib/strings/en";
import { localModelStrings as state } from "@/lib/strings/local-llm";
import { localChoiceStrings as t } from "@/lib/strings/pipelines";

const secs = (ms: number | null | undefined) => (ms == null ? "n/a" : `${(ms / 1000).toFixed(0)} s`);
const share = (v: number | null | undefined) => (v == null ? "n/a" : `${Math.round(v * 100)}%`);
const sizeText = (mb: number | null | undefined) =>
	mb == null ? null : mb >= 1024 ? `${(mb / 1024).toFixed(1)} GB` : `${mb} MB`;

/** `ok` when ready, `warn` while it is on its way, `bad` when it is down, plain when not set up. */
export function stateTone(s: string | undefined): "ok" | "warn" | "bad" | "default" {
	if (s === "ready") return "ok";
	if (s === "pulling" || s === "starting") return "warn";
	if (s === "down") return "bad";
	return "default";
}

/**
 * For a profile that answers on this server's CPU: the local model's live state ("Downloading the
 * model…") and the measured local models with their speed and how many facts they keep in English,
 * Hindi and Hinglish. Choosing a row hands the model to the form.
 */
export function LocalModelPicker({ model, onPick }: { model: string; onPick: (m: string) => void }) {
	const status = useQuery(localLlmQuery);
	const bench = useQuery(answerBenchmarksQuery);
	const rows = bench.data?.local?.models ?? [];
	const current = model.trim() || status.data?.model || "";
	const s = status.data;
	return (
		<section aria-label={t.title} className="grid gap-2 rounded-md border border-border bg-muted p-3">
			<div className="flex flex-wrap items-baseline justify-between gap-2">
				<h3 className="m-0 text-[13px] font-semibold">{t.title}</h3>
				<Link to="/benchmarks" className="text-[12.5px] text-primary hover:underline">
					Benchmarks
				</Link>
			</div>
			<p className="m-0 text-[12.5px] text-muted-foreground">{t.lead}</p>
			{status.isError ? (
				<p className="m-0 text-[12.5px] text-destructive">{t.loadError}</p>
			) : s ? (
				<div className="grid gap-1" data-testid="local-state">
					<div className="flex flex-wrap items-center gap-2 text-[13px]">
						<span className="font-semibold">{t.state}</span>
						<Badge variant={stateTone(s.state)}>{state.states[s.state] ?? s.state}</Badge>
					</div>
					<p className="m-0 text-[12.5px] text-foreground-2">{state.body[s.state]}</p>
				</div>
			) : null}
			<h4 className="m-0 mt-1 text-[12.5px] font-semibold">{t.measuredTitle}</h4>
			{rows.length === 0 ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">{t.none}</p>
			) : (
				<>
					<p className="m-0 text-[12.5px] text-muted-foreground">{t.measuredLead}</p>
					<ul aria-label={t.measuredTitle} className="m-0 grid list-none gap-2 p-0">
						{rows.map((m) => {
							const selected = m.model === current;
							const size = sizeText(m.size_mb);
							return (
								<li
									key={m.model}
									data-selected={selected || undefined}
									className="grid gap-1.5 rounded-md border border-border bg-card p-2.5"
								>
									<div className="flex flex-wrap items-center gap-1.5">
										<code className="font-mono text-[12.5px] font-bold break-all">{m.model}</code>
										{m.passed ? <Badge variant="ok">{t.passed}</Badge> : <Badge variant="warn">{t.slowLess}</Badge>}
										{size ? <Badge>{fill(t.size, { size })}</Badge> : null}
									</div>
									<span className="text-[12.5px] text-foreground-2">
										{fill(t.speed, { first: secs(m.ttft_p50_ms), total: secs(m.total_p50_ms) })}
									</span>
									<span className="text-[12.5px] text-foreground-2">
										{fill(t.facts, { en: share(m.fact.en), hi: share(m.fact.hi), hl: share(m.fact["hi-Latn"]) })}
									</span>
									<div>
										<Button
											type="button"
											size="sm"
											variant={selected ? "ghost" : "outline"}
											disabled={selected}
											aria-label={`${selected ? t.inUse : t.use}: ${m.model}`}
											onClick={() => onPick(m.model)}
										>
											{selected ? t.inUse : t.use}
										</Button>
									</div>
								</li>
							);
						})}
					</ul>
				</>
			)}
		</section>
	);
}
