import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { describedBy, Field } from "@/components/auth/Field";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { qk } from "@/lib/api/query-keys";
import { assistantStrings as t } from "@/lib/strings/assistant";
import { fill } from "@/lib/strings/en";
import type { CorpusDetail } from "../../kbs/api";
import { Panel } from "../../kbs/ui";
import { compileConfig } from "../api";
import { TextControl } from "../controls";
import { SectionShell } from "../SectionShell";
import { advancedSchema, LANG_KEYS, TURN_LABELS } from "../schemas";
import type { AssistantState, CompileSample } from "../types";
import { useDraftSection } from "../useDraftSection";
import { fnv1a } from "./useRuleBudget";

const EXTRA_MAX = 15_000;
type Office = CompileSample["office_hours"];

/** Expert mode: the read-only compiled prompt for a sample turn, and the expert instructions editor. */
export function AdvancedSection({ kb, state }: { kb: CorpusDetail; state: AssistantState }) {
	const section = useDraftSection(kb.id, state, "advanced", advancedSchema);
	const [editing, setEditing] = useState(false);
	const [label, setLabel] = useState<CompileSample["label"]>("business_question");
	const [language, setLanguage] = useState<CompileSample["language"]>("en");
	const [office, setOffice] = useState<Office>("unknown");
	const [page, setPage] = useState("");
	const ids = { label: useId(), lang: useId(), office: useId(), page: useId() };
	const config = section.config;
	const sample = { label, language, office_hours: office, ...(page.trim() ? { page_url: page.trim() } : {}) };
	const q = useQuery({
		queryKey: qk.assistant.compile(kb.id, `expert-${fnv1a(JSON.stringify([config, sample]))}`),
		queryFn: ({ signal }) => compileConfig(kb.id, config, sample, signal),
		staleTime: 60_000,
		retry: false,
	});
	const names = t.expert.sectionTitles as Record<string, string>;
	return (
		<SectionShell section={section}>
			<Panel title={t.expert.viewTitle} sub={t.expert.viewSub}>
				<div className="grid gap-4 p-4">
					<div className="grid gap-3 min-[700px]:grid-cols-4">
						<Field id={ids.label} label={t.expert.turn}>
							<NativeSelect
								id={ids.label}
								value={label}
								onChange={(e) => setLabel(e.target.value as CompileSample["label"])}
							>
								{TURN_LABELS.map((v) => (
									<option key={v} value={v}>
										{t.rules.turnTypes[v]}
									</option>
								))}
							</NativeSelect>
						</Field>
						<Field id={ids.lang} label={t.expert.language}>
							<NativeSelect
								id={ids.lang}
								value={language}
								onChange={(e) => setLanguage(e.target.value as CompileSample["language"])}
							>
								{LANG_KEYS.map((v) => (
									<option key={v} value={v}>
										{t.langs[v]}
									</option>
								))}
							</NativeSelect>
						</Field>
						<Field id={ids.office} label={t.expert.office}>
							<NativeSelect id={ids.office} value={office} onChange={(e) => setOffice(e.target.value as Office)}>
								{(Object.keys(t.expert.offices) as Office[]).map((v) => (
									<option key={v} value={v}>
										{t.expert.offices[v]}
									</option>
								))}
							</NativeSelect>
						</Field>
						<Field id={ids.page} label={t.expert.page} hint={t.expert.pageHint}>
							<Input
								id={ids.page}
								type="url"
								inputMode="url"
								aria-describedby={describedBy(ids.page, { hint: true })}
								value={page}
								onChange={(e) => setPage(e.target.value)}
							/>
						</Field>
					</div>
					{q.isPending ? <p className="m-0 text-[13px] text-muted-foreground">{t.expert.loading}</p> : null}
					{q.isError ? <ErrorState error={q.error} onRetry={() => void q.refetch()} /> : null}
					{q.data ? (
						<div className="grid gap-3">
							{q.data.sections.map((s) => (
								<section key={s.name} className="grid gap-1 rounded-lg border border-border">
									<div className="flex flex-wrap items-baseline justify-between gap-2 border-b border-border px-3 py-2">
										<h3 className="m-0 text-[13.5px] font-semibold">{names[s.name] ?? s.name}</h3>
										<span className="font-mono text-[11.5px] text-muted-foreground">
											{fill(t.expert.tokens, { n: s.tokens })}
										</span>
									</div>
									{s.name === "grounding" ? (
										<p className="m-0 px-3 pt-1 text-[12px] text-muted-foreground">{t.expert.groundingLocked}</p>
									) : null}
									<pre className="m-0 max-h-[260px] overflow-auto whitespace-pre-wrap px-3 py-2 font-mono text-[12px] leading-[1.5]">
										{s.text || t.expert.empty}
									</pre>
								</section>
							))}
							<div className="grid gap-1 text-[13px]">
								<b>{t.expert.firing}</b>
								{q.data.rules_fired.length ? (
									<ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
										{q.data.rules_fired.map((r) => (
											<li key={r} className="rounded-full bg-secondary px-2 py-0.5 font-mono text-[11.5px]">
												{r}
											</li>
										))}
									</ul>
								) : (
									<span className="text-muted-foreground">{t.expert.noneFiring}</span>
								)}
								<span className="text-muted-foreground">{fill(t.expert.total, { n: q.data.total_tokens })}</span>
								<span className="text-muted-foreground">{fill(t.expert.cap, { n: q.data.max_tokens })}</span>
								<span className="text-muted-foreground">
									{fill(t.expert.worst, {
										used: new Intl.NumberFormat("en-IN").format(q.data.prompt_tokens),
										budget: new Intl.NumberFormat("en-IN").format(q.data.prompt_budget),
									})}
								</span>
							</div>
						</div>
					) : null}
				</div>
			</Panel>
			<Panel title={t.expert.editTitle} sub={t.expert.editHint}>
				<div className="grid gap-3 p-4">
					{editing ? (
						<>
							<TextControl
								name="extra_instructions"
								label={t.expert.editLabel}
								rows={10}
								maxLength={EXTRA_MAX}
								counter
							/>
							<div>
								<Button type="button" variant="ghost" size="sm" onClick={() => setEditing(false)}>
									{t.expert.lock}
								</Button>
							</div>
						</>
					) : (
						<div>
							<Button type="button" variant="outline" onClick={() => setEditing(true)}>
								{t.expert.editToggle}
							</Button>
						</div>
					)}
				</div>
			</Panel>
		</SectionShell>
	);
}
