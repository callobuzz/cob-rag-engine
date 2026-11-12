import { useQuery } from "@tanstack/react-query";
import { SlidersHorizontal } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import type { Corpus } from "@/lib/api/queries";
import { formatNumber } from "@/lib/format/number";
import { qualityStrings } from "@/lib/strings/insights";
import { qualityEvalsStrings } from "@/lib/strings/quality-evals";
import { optimizeRunsQuery, useTriggerOptimize } from "./api";
import { RunDialog, RunsList } from "./runs";

const t = qualityStrings.optimize;
const d = qualityStrings.optimizeDialog;

/** Optimisation runs (admin): list with paging, "Run optimisation" → the new run's detail. */
export function OptimizeTab({
	kb,
	addon,
	page,
	onPage,
	onOpen,
}: {
	kb: Corpus;
	addon: boolean;
	page: number;
	onPage: (p: number) => void;
	onOpen: (id: string) => void;
}) {
	const q = useQuery(optimizeRunsQuery(page));
	const trigger = useTriggerOptimize();
	const [open, setOpen] = useState(false);
	return (
		<>
			<RunsList
				title={t.title}
				sub={t.sub}
				rows={q.data?.items}
				total={q.data?.total ?? 0}
				pending={q.isPending}
				error={q.error}
				onRetry={() => void q.refetch()}
				page={page}
				onPage={onPage}
				onOpen={onOpen}
				empty={t.empty}
				errorTitle={t.error}
				labels={{
					started: t.colStarted,
					kb: t.colKb,
					amount: t.colTrials,
					status: t.colStatus,
					finished: t.colFinished,
					anyKb: t.anyKb,
					open: t.open,
				}}
				amount={(r) => (r.n_trials == null ? "—" : formatNumber(r.n_trials))}
				aside={
					<Button
						type="button"
						variant="default"
						disabled={!addon}
						title={addon ? undefined : qualityEvalsStrings.addon.runDisabled}
						onClick={() => setOpen(true)}
					>
						<SlidersHorizontal aria-hidden />
						{t.run}
					</Button>
				}
			/>
			<RunDialog
				open={open}
				onOpenChange={setOpen}
				defaultKb={kb.id}
				pending={trigger.isPending}
				strings={{
					title: d.title,
					lead: d.lead,
					kb: d.kb,
					anyKb: d.anyKb,
					number: d.trials,
					numberHint: d.trialsHint,
					bad: d.badTrials,
					submit: d.submit,
				}}
				onSubmit={async (v) => {
					const r = await trigger.mutateAsync({ corpus_id: v.corpus_id, n_trials: v.n });
					toast.success(d.queued);
					onOpen(r.optimization_run_id);
				}}
			/>
		</>
	);
}
