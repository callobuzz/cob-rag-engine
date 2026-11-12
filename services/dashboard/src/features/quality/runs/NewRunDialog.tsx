import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { useWatch } from "react-hook-form";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useErrorText } from "@/features/insights/ui";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { formatCost } from "@/lib/format/cost";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { fill } from "@/lib/strings/en";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import { useEvalAddon } from "../addon";
import { criteriaQuery, type Estimate, type GoldenSet, useEstimate, useStartRun } from "../evals-api";

const n = t.newRun;
const a = t.addon;

const schema = z
	.object({
		set_id: z.string().min(1),
		mode: z.enum(["fast", "full"]),
		name: z.string().trim().max(120),
		assistant: z.string().regex(/^(published|draft|\d+)$/),
		preset: z.enum(["", "economy", "standard", "premium"]),
		confirmed: z.boolean(),
	})
	.superRefine((v, ctx) => {
		if (v.mode === "full" && !v.confirmed)
			ctx.addIssue({ code: "custom", path: ["confirmed"], message: n.confirmRequired });
	});
type Form = z.input<typeof schema>;

/**
 * Start a run: pick a set, a mode and (for a full run) a settings snapshot, see the estimated cost
 * and confirm it. A quick check needs no estimate; the server refuses a full run whose confirmed
 * cost is below its own estimate (409 `eval_cost_unconfirmed`).
 */
export function NewRunDialog({
	kb,
	sets,
	initialSetId,
	onStarted,
	onClose,
}: {
	kb: string;
	sets: GoldenSet[];
	initialSetId?: string;
	onStarted: (id: string) => void;
	onClose: () => void;
}) {
	const ids = { set: useId(), name: useId(), assistant: useId(), preset: useId(), confirm: useId() };
	const addon = useEvalAddon();
	// Without the add-on a full run can still judge the knowledge base's own criteria.
	const criteria = useQuery({ ...criteriaQuery(kb), enabled: !addon });
	const noFull = !addon && criteria.isSuccess && !criteria.data.items.some((c) => c.enabled);
	const estimate = useEstimate(kb);
	const start = useStartRun(kb);
	const errorText = useErrorText();
	const [est, setEst] = useState<Estimate | null>(null);
	const [estError, setEstError] = useState<string | null>(null);
	const form = useAppForm<Form>({
		resolver: zodResolver(schema),
		defaultValues: {
			set_id: initialSetId ?? sets.find((s) => s.is_default)?.id ?? sets[0]?.id ?? "",
			mode: "fast",
			name: "",
			assistant: "published",
			preset: "",
			confirmed: false,
		},
	});
	const [mode, confirmed] = useWatch({ control: form.control, name: ["mode", "confirmed"] });
	// A changed set, mode, assistant or preset makes the old estimate (and its tick) stale.
	const resetEstimate = () => {
		setEst(null);
		form.setValue("confirmed", false);
	};
	const snapshot = (v: Form) => ({
		assistant: /^\d+$/.test(v.assistant) ? Number(v.assistant) : (v.assistant as "published" | "draft"),
		...(v.preset ? { retrieval: { preset: v.preset } } : {}),
	});
	const submit = useAppSubmit(form, async (v) => {
		if (v.mode === "full" && !est) {
			form.setError("confirmed", { message: n.estimateFirst });
			return;
		}
		const run = await start.mutateAsync({
			set_id: v.set_id,
			mode: v.mode,
			settings: snapshot(v),
			...(v.name ? { name: v.name } : {}),
			...(v.mode === "full" && est ? { confirm_cost_usd: est.total_usd } : {}),
		});
		onStarted(run.id);
		onClose();
	});
	const full = mode === "full";
	const errs = form.formState.errors;
	const total = est ? formatCost(est.total_usd, est.unpriced_models.length === 0) : "";
	return (
		<Dialog open onOpenChange={(o) => (o ? null : onClose())}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[560px]">
				<DialogHeader>
					<DialogTitle>{n.title}</DialogTitle>
					<DialogDescription className="sr-only">{n.title}</DialogDescription>
				</DialogHeader>
				<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-3">
					{submit.error ? <KbErrorAlert error={submit.error} /> : null}
					<Field id={ids.set} label={n.set}>
						<NativeSelect id={ids.set} {...form.register("set_id", { onChange: resetEstimate })}>
							{sets.map((s) => (
								<option key={s.id} value={s.id}>
									{s.name}
								</option>
							))}
						</NativeSelect>
					</Field>
					<fieldset className="grid gap-1 border-0 p-0">
						<legend className="mb-1 text-[13px] font-semibold">{n.mode}</legend>
						<label className="flex gap-2">
							<input type="radio" value="fast" {...form.register("mode", { onChange: resetEstimate })} />
							<span>
								<b>{n.quick}</b> {n.quickHint}
							</span>
						</label>
						<label className="flex gap-2">
							<input
								type="radio"
								value="full"
								disabled={noFull}
								{...form.register("mode", { onChange: resetEstimate })}
							/>
							<span>
								<b>{n.full}</b> {n.fullHint}
							</span>
						</label>
						{noFull ? (
							<small role="note" className="text-muted-foreground">
								{a.fullNeedsCriteria}
							</small>
						) : !addon ? (
							<small role="note" className="text-muted-foreground">
								{a.fullCriteriaOnly}
							</small>
						) : null}
					</fieldset>
					<Field id={ids.name} label={n.name} hint={n.nameHint}>
						<Input id={ids.name} aria-describedby={describedBy(ids.name, { hint: true })} {...form.register("name")} />
					</Field>
					<Field id={ids.assistant} label={n.assistant}>
						<NativeSelect id={ids.assistant} {...form.register("assistant", { onChange: resetEstimate })}>
							<option value="published">{n.assistantOptions.published}</option>
							<option value="draft">{n.assistantOptions.draft}</option>
						</NativeSelect>
					</Field>
					<Field id={ids.preset} label={n.preset}>
						<NativeSelect id={ids.preset} {...form.register("preset", { onChange: resetEstimate })}>
							{Object.entries(n.presetOptions).map(([value, label]) => (
								<option key={value} value={value}>
									{label}
								</option>
							))}
						</NativeSelect>
					</Field>
					{full ? (
						<div className="grid gap-2 rounded-lg border border-border p-3">
							<Button
								type="button"
								disabled={estimate.isPending}
								onClick={() => {
									setEstError(null);
									const v = form.getValues();
									estimate.mutate(
										{ set_id: v.set_id, mode: "full", settings: snapshot(v) },
										{
											onSuccess: (e) => {
												setEst(e);
												form.setValue("confirmed", false);
											},
											onError: (e) => setEstError(errorText(e)),
										},
									);
								}}
							>
								{n.estimate}
							</Button>
							{estError ? (
								<p role="alert" className="m-0 text-destructive">
									{estError}
								</p>
							) : null}
							{est ? (
								<>
									<p role="status" className="m-0">
										{fill(n.estimateLine, {
											total,
											answer: formatCost(est.answer_usd, est.unpriced_models.length === 0),
											judge: formatCost(est.judge_usd, est.unpriced_models.length === 0),
											n: est.questions,
										})}
									</p>
									{est.unpriced_models.length ? (
										<p className="m-0 text-foreground-2">
											{fill(n.unpriced, { models: est.unpriced_models.join(", ") })}
										</p>
									) : null}
									<label htmlFor={ids.confirm} className="flex gap-2">
										<input
											id={ids.confirm}
											type="checkbox"
											aria-invalid={!!errs.confirmed}
											{...form.register("confirmed")}
										/>
										{fill(n.confirm, { total })}
									</label>
								</>
							) : null}
							{errs.confirmed?.message ? (
								<small role="alert" className="font-medium text-destructive">
									{errs.confirmed.message}
								</small>
							) : null}
						</div>
					) : null}
					<SubmitButton
						variant="default"
						pending={submit.isSubmitting}
						locked={submit.locked}
						aria-disabled={full && (!est || !confirmed) ? true : undefined}
					>
						{n.start}
					</SubmitButton>
				</form>
			</DialogContent>
		</Dialog>
	);
}
