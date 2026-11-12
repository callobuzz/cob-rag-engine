import { CircleCheck, CircleX, ShieldAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Panel } from "@/features/kbs/ui";
import { PipelineStrip } from "@/features/playground/PipelineStrip";
import { SourcesPanel } from "@/features/playground/SourcesPanel";
import type { BotMsg } from "@/features/playground/session";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { TURN_LABELS, type TurnLabel } from "../schemas";
import { traceOf, two } from "./trace";

const isLabel = (v: string | undefined): v is TurnLabel => !!v && (TURN_LABELS as readonly string[]).includes(v);
const turnName = (v: string | undefined) => (isLabel(v) ? t.rules.turnTypes[v] : (v ?? "—"));
const routeName = (v: string | undefined) => (v ? ((t.trace.routes as Record<string, string>)[v] ?? v) : "—");
const langName = (v: string | undefined) => (v ? ((t.langs as Record<string, string>)[v] ?? v) : "—");

function Row({ label, children }: { label: string; children: ReactNode }) {
	return (
		<div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5 text-[13px]">
			<dt className="text-muted-foreground">{label}</dt>
			<dd className="m-0 font-medium">{children}</dd>
		</div>
	);
}

function Card({ title, children }: { title: string; children: ReactNode }) {
	return (
		<Panel title={title}>
			<div className="grid gap-2 p-4">{children}</div>
		</Panel>
	);
}

/** Pass or fail in words and an icon, never by colour alone. */
function Verdict({ ok, yes, no }: { ok: boolean | null | undefined; yes: string; no: string }) {
	if (ok === null || ok === undefined) return <span className="text-muted-foreground">{t.trace.unknown}</span>;
	const Icon = ok ? CircleCheck : CircleX;
	return (
		<span className={cn("inline-flex items-center gap-1 font-semibold", ok ? "text-success" : "text-destructive")}>
			<Icon className="size-4" aria-hidden />
			{ok ? yes : no}
		</span>
	);
}

/**
 * "How this answer was made" for the selected preview answer. Owners get the full trace; a member
 * gets the turn type, route and language only, with the reason. The numbers come from the engine's
 * own trace; nothing here is computed.
 */
export function TracePanel({
	msg,
	owner,
	highlight,
}: {
	msg: BotMsg | undefined;
	owner: boolean;
	highlight: number | null;
}) {
	const trace = traceOf(msg);
	const label = trace?.label?.value ?? msg?.meta?.label ?? msg?.done?.label;
	const route = msg?.done?.route ?? msg?.meta?.route;
	const language = trace?.language?.code ?? msg?.done?.language ?? msg?.meta?.language;
	const r = trace?.retrieval;
	const claims = trace?.claims ?? [];
	const offers = trace?.policy?.offers ?? [];
	const pii = trace?.pii ?? [];
	const injection = trace?.safety?.injection ?? [];
	return (
		<aside aria-label={t.trace.title} className="grid min-w-0 gap-4">
			{!msg ? (
				<Card title={t.trace.title}>
					<p className="m-0 text-[13px] text-muted-foreground">{t.trace.empty}</p>
				</Card>
			) : (
				<>
					<Card title={t.trace.turn}>
						<dl className="m-0 grid gap-1.5">
							<Row label={t.trace.turnType}>{turnName(label)}</Row>
							{trace?.label ? (
								<Row label={t.trace.turnSource}>
									{(t.trace.turnSources as Record<string, string>)[trace.label.source] ?? trace.label.source}
									{" · "}
									{t.trace.confidence} {two(trace.label.confidence)}
								</Row>
							) : null}
							<Row label={t.trace.language}>
								{langName(language)}
								{trace?.language?.script ? ` · ${trace.language.script}` : ""}
							</Row>
							<Row label={t.trace.route}>{routeName(route)}</Row>
						</dl>
						{!owner ? <p className="m-0 text-[12.5px] text-muted-foreground">{t.trace.memberOnly}</p> : null}
						{owner && !trace && msg.status === "streaming" ? (
							<p className="m-0 text-[12.5px] text-muted-foreground">{t.trace.waiting}</p>
						) : null}
					</Card>
					{owner ? (
						<>
							<Card title={t.trace.gate}>
								<dl className="m-0 grid gap-1.5">
									<Row label={t.trace.top}>
										<span data-testid="trace-top">{r?.top_score != null ? two(r.top_score) : "—"}</span>
									</Row>
									<Row label={t.trace.minimum}>
										<span data-testid="trace-threshold">{r?.threshold != null ? two(r.threshold) : "—"}</span>
										{r?.threshold_source ? ` (${t.trace.minimumSources[r.threshold_source]})` : ""}
									</Row>
									<Row label={t.trace.gate}>
										<Verdict ok={r?.gate_passed} yes={t.trace.passed} no={t.trace.failed} />
									</Row>
								</dl>
								{r?.queries?.length ? (
									<p className="m-0 text-[12.5px] text-muted-foreground">
										{t.trace.queries}: {r.queries.join(" · ")}
									</p>
								) : null}
							</Card>
							<SourcesPanel
								sources={msg.sources}
								scoresState={msg.scoresState}
								highlight={highlight}
								idPrefix={`trace-${msg.id}`}
								sub={t.trace.sources}
							/>
							<Card title={t.trace.facts}>
								{claims.length === 0 ? (
									<p className="m-0 text-[13px] text-muted-foreground">{t.trace.factsNone}</p>
								) : (
									<ul className="m-0 grid list-none gap-2 p-0">
										{claims.map((c, i) => (
											// biome-ignore lint/suspicious/noArrayIndexKey: a static list of one answer's facts; a sentence can repeat
											<li key={`${i}-${c.sentence}`} className="grid gap-0.5 text-[13px]">
												<span className="inline-flex flex-wrap items-center gap-2">
													<b>{(t.trace.factKinds as Record<string, string>)[c.kind] ?? c.kind}</b>
													<span className="font-mono">{c.value}</span>
													<span
														className={cn(
															"inline-flex items-center gap-1 font-semibold",
															c.action === "kept" ? "text-success" : "text-warning",
														)}
													>
														{c.action === "kept" ? (
															<CircleCheck className="size-4" aria-hidden />
														) : (
															<ShieldAlert className="size-4" aria-hidden />
														)}
														{t.trace[c.action]}
													</span>
												</span>
												{c.action === "removed" ? (
													<span className="text-[12px] text-muted-foreground">
														{t.trace.removedHidden}: “{c.sentence}”
													</span>
												) : null}
											</li>
										))}
									</ul>
								)}
							</Card>
							<Card title={t.trace.rules}>
								{trace?.rules_fired?.length ? (
									<ul className="m-0 grid list-none gap-1.5 p-0">
										{trace.rules_fired.map((x, i) => (
											// biome-ignore lint/suspicious/noArrayIndexKey: a static list; a rule id can repeat
											<li key={`${i}-${x.id}`} className="text-[13px]">
												<span className="font-mono text-[12px]">{x.id}</span>
												{x.instruction ? ` ${x.instruction}` : null}
											</li>
										))}
									</ul>
								) : (
									<p className="m-0 text-[13px] text-muted-foreground">{t.trace.rulesNone}</p>
								)}
							</Card>
							<Card title={t.trace.safety}>
								{pii.length === 0 && injection.length === 0 && !trace?.safety?.abuse ? (
									<p className="m-0 text-[13px] text-muted-foreground">{t.trace.safetyOk}</p>
								) : (
									<ul className="m-0 grid list-none gap-1 p-0 text-[13px]">
										{pii.length ? (
											<li>
												{fill(t.trace.pii, {
													list: pii.map((k) => (t.trace.piiKinds as Record<string, string>)[k] ?? k).join(", "),
												})}
											</li>
										) : null}
										{injection.length ? <li>{fill(t.trace.injection, { list: injection.join(", ") })}</li> : null}
										{trace?.safety?.abuse ? <li>{fill(t.trace.abuse, { n: trace.safety.abuse })}</li> : null}
									</ul>
								)}
							</Card>
							<Card title={t.trace.offers}>
								{offers.length === 0 ? (
									<p className="m-0 text-[13px] text-muted-foreground">{t.trace.offersNone}</p>
								) : (
									<ul className="m-0 grid list-none gap-1 p-0 text-[13px]">
										{offers.map((o) => (
											<li key={`${o.kind}-${o.reason}`}>
												<b>{(t.trace.offerKinds as Record<string, string>)[o.kind] ?? o.kind}</b>
												{o.reason ? ` · ${o.reason.replaceAll("_", " ")}` : ""}
											</li>
										))}
									</ul>
								)}
							</Card>
							{trace?.prompt ? (
								<Card title={t.trace.prompt}>
									<details>
										<summary className="cursor-pointer text-[13px] font-semibold">
											{fill(t.trace.promptTokens, { n: trace.prompt.tokens })}
										</summary>
										<div className="mt-2 grid gap-2">
											<b className="text-[12.5px]">{t.trace.promptSystem}</b>
											<pre className="m-0 max-h-[240px] overflow-auto whitespace-pre-wrap rounded-md bg-secondary p-2 font-mono text-[12px]">
												{trace.prompt.system}
											</pre>
											<b className="text-[12.5px]">{t.trace.promptUser}</b>
											<pre className="m-0 max-h-[240px] overflow-auto whitespace-pre-wrap rounded-md bg-secondary p-2 font-mono text-[12px]">
												{trace.prompt.user}
											</pre>
										</div>
									</details>
								</Card>
							) : null}
							<PipelineStrip
								timings={msg.times.steps ?? undefined}
								writeMs={msg.times.stages?.write ?? null}
								totalMs={msg.latencyMs}
								state={msg.timingsState}
							/>
						</>
					) : null}
				</>
			)}
		</aside>
	);
}
