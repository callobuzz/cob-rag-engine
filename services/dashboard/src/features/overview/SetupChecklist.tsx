import { Link } from "@tanstack/react-router";
import { Check, CircleHelp } from "lucide-react";
import { type ReactNode, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { readPref, writePref } from "@/lib/auth/storage";
import { formatNumber, formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { cn } from "@/lib/utils";
import { plural } from "./kpis";
import { List } from "./ui";

const t = overviewStrings.checklist;

export type StepId = "kb" | "content" | "playground" | "widget" | "agent";
export type StepState = "done" | "todo" | "unknown";

export interface ChecklistInput {
	/** `corpora.total`, or undefined while loading / on error. */
	kbs: number | undefined;
	/** Σ document_count. */
	documents: number | undefined;
	/** `/analytics/queries/count`; null when the call failed. */
	questions: number | null | undefined;
	/** `/tenants/me/plan` `usage.widget_keys`; null when the call failed. */
	widgetKeys: number | null | undefined;
	/** The manual "Connect an agent" flag (the API has no signal for it). */
	agentDone: boolean;
}

export interface Step {
	id: StepId;
	state: StepState;
	label: string;
}

const known = (v: number | null | undefined, ok: (n: number) => boolean): StepState =>
	v == null ? "unknown" : ok(v) ? "done" : "todo";

/** The five steps (D2 decisions), each from the one API signal that can detect it. */
export function checklistSteps(i: ChecklistInput): Step[] {
	return [
		{ id: "kb", state: known(i.kbs, (n) => n > 0), label: t.steps.kb },
		{
			id: "content",
			state: known(i.documents, (n) => n > 0),
			label: i.documents
				? fill(t.steps.contentDone, { n: plural(overviewStrings.documents, i.documents, formatNumber(i.documents)) })
				: t.steps.content,
		},
		{ id: "playground", state: known(i.questions, (n) => n > 0), label: t.steps.playground },
		{ id: "widget", state: known(i.widgetKeys, (n) => n > 0), label: t.steps.widget },
		{ id: "agent", state: i.agentDone ? "done" : "todo", label: t.steps.agent },
	];
}

export const agentFlagKey = (tenantId: string) => `cob.checklist.mcp.${tenantId}`;
export const hiddenFlagKey = (tenantId: string) => `cob.checklist.hidden.${tenantId}`;

export function useChecklistFlags(tenantId: string) {
	const [agentDone, setAgent] = useState(() => readPref(agentFlagKey(tenantId)) === "1");
	const [hidden, setHidden] = useState(() => readPref(hiddenFlagKey(tenantId)) === "1");
	return {
		agentDone,
		hidden,
		markAgentDone: () => {
			writePref(agentFlagKey(tenantId), "1");
			setAgent(true);
		},
		hide: () => {
			writePref(hiddenFlagKey(tenantId), "1");
			setHidden(true);
		},
	};
}

const LINK: Record<StepId, string> = {
	kb: "/kbs",
	content: "/add",
	playground: "/playground",
	widget: "/widget",
	agent: "/agents",
};

/** One checklist line: a tick circle, the label (struck through when done), then the actions. */
export function ChecklistRow({
	id,
	state,
	label,
	children,
}: {
	id: string;
	state: StepState;
	label: string;
	children?: ReactNode;
}) {
	return (
		<li
			data-testid={`step-${id}`}
			data-state={state}
			className="flex min-w-0 flex-wrap items-center gap-3 border-b border-border px-4 py-[11px] last:border-b-0"
		>
			<span
				className={cn(
					"grid size-[22px] shrink-0 place-items-center rounded-full border-[1.5px] border-input",
					state === "done" && "border-success bg-success text-background",
				)}
			>
				{state === "done" ? (
					<Check className="size-[13px]" strokeWidth={3} aria-hidden />
				) : state === "unknown" ? (
					<CircleHelp className="size-[13px] text-muted-foreground" aria-hidden />
				) : null}
			</span>
			<span
				className={cn(
					"min-w-0 flex-1 font-semibold",
					state === "done" && "text-foreground-2 line-through decoration-muted-foreground",
				)}
			>
				{label}
				<span className="sr-only"> · {state === "done" ? t.done : state === "unknown" ? t.unknown : t.notDone}</span>
				{state === "unknown" ? (
					<span aria-hidden className="ml-1.5 text-[12px] font-normal text-muted-foreground">
						({t.unknown})
					</span>
				) : null}
			</span>
			{children}
		</li>
	);
}

/** Mockup `.checklist`: hidden once all five are done, or after "Hide". */
export function SetupChecklist({
	tenant,
	steps,
	onMarkAgentDone,
	onHide,
}: {
	tenant: string;
	steps: Step[];
	onMarkAgentDone: () => void;
	onHide: () => void;
}) {
	const done = steps.filter((s) => s.state === "done").length;
	if (done === steps.length) return null;
	return (
		<Panel
			title={t.title}
			sub={fill(t.sub, { tenant, done, total: steps.length })}
			className="checklist"
			aside={
				<div className="flex items-center gap-2">
					<Badge variant="info">{formatPercent(done / steps.length)}</Badge>
					<Button size="sm" variant="ghost" onClick={onHide} aria-label={t.hideLabel}>
						{t.hide}
					</Button>
				</div>
			}
		>
			<List>
				{steps.map((s) => (
					<ChecklistRow key={s.id} id={s.id} state={s.state} label={s.label}>
						{s.state !== "done" ? (
							<span className="flex gap-1.5">
								<Link to={LINK[s.id]} className={buttonVariants({ size: "sm" })}>
									{t.actions[s.id]}
								</Link>
								{s.id === "agent" ? (
									<Button size="sm" variant="ghost" onClick={onMarkAgentDone} aria-label={t.markDoneLabel}>
										{t.markDone}
									</Button>
								) : null}
							</span>
						) : null}
					</ChecklistRow>
				))}
			</List>
		</Panel>
	);
}
