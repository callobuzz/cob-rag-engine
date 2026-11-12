import { Link } from "@tanstack/react-router";
import { useSwitchScope } from "@/components/shell/ScopeSwitcher";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { ADD_CUSTOMER } from "@/features/customers/first-customer";
import { Panel } from "@/features/kbs/ui";
import type { AdminTenant } from "@/lib/api/accounts-types";
import { formatPercent } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { overviewStrings } from "@/lib/strings/overview";
import { ChecklistRow, type StepState } from "./SetupChecklist";
import { List } from "./ui";

const t = overviewStrings.adminChecklist;

export type AdminStepId = "workspace" | "kb" | "widget" | "customer";

export interface AdminStep {
	id: AdminStepId;
	state: StepState;
	label: string;
}

export interface AdminChecklistInput {
	/** The operator's own workspace; the list is about getting it going, so without one there is no list. */
	workspace: AdminTenant;
	/** Widget keys of the workspace (`/admin/tenants/{id}/plan`); null when that call failed. */
	widgetKeys: number | null | undefined;
	/** Active customers (the operator workspace is not one). */
	customers: number | undefined;
}

const known = (v: number | null | undefined): StepState => (v == null ? "unknown" : v > 0 ? "done" : "todo");

/** The four first-run steps of the operator view, each from the one API signal that can detect it. */
export function adminChecklistSteps(i: AdminChecklistInput): AdminStep[] {
	return [
		{ id: "workspace", state: "done", label: t.steps.workspace },
		{ id: "kb", state: known(i.workspace.corpus_count), label: t.steps.kb },
		{ id: "widget", state: known(i.widgetKeys), label: t.steps.widget },
		{ id: "customer", state: known(i.customers), label: t.steps.customer },
	];
}

/**
 * Operator view, first run: ✓ your workspace is ready → create its first knowledge base → put its
 * widget on your own site → add your first customer. The middle two switch into the workspace
 * first (only a session sign-in can; with the admin key the step says so instead of a button).
 * Hidden once all four are done, or after "Hide".
 */
export function AdminChecklist({
	steps,
	workspaceName,
	canSwitch,
	onHide,
}: {
	steps: AdminStep[];
	workspaceName: string;
	canSwitch: boolean;
	onHide: () => void;
}) {
	const switchScope = useSwitchScope();
	const done = steps.filter((s) => s.state === "done").length;
	if (done === steps.length) return null;
	return (
		<Panel
			title={t.title}
			sub={`${fill(t.workspaceNote, { name: workspaceName })} · ${fill(t.sub, { done, total: steps.length })}`}
			className="checklist"
			aside={
				<div className="flex items-center gap-2">
					<Badge variant="info">{formatPercent(done / steps.length)}</Badge>
					<Button size="sm" variant="ghost" onClick={onHide} aria-label={t.hideLabel}>
						{overviewStrings.checklist.hide}
					</Button>
				</div>
			}
		>
			<List>
				{steps.map((s) => (
					<ChecklistRow key={s.id} id={`admin-${s.id}`} state={s.state} label={s.label}>
						{s.state === "done" ? null : s.id === "customer" ? (
							<Link {...ADD_CUSTOMER} className={buttonVariants({ size: "sm" })}>
								{t.actions.customer}
							</Link>
						) : s.id === "kb" || s.id === "widget" ? (
							canSwitch ? (
								<Button
									size="sm"
									variant="default"
									onClick={() => void switchScope("workspace", s.id === "kb" ? "newKb" : "widget")}
								>
									{s.id === "kb" ? t.actions.kb : t.actions.widget}
								</Button>
							) : (
								<span className="text-[12px] text-muted-foreground">{t.sessionOnly}</span>
							)
						) : null}
					</ChecklistRow>
				))}
			</List>
		</Panel>
	);
}
