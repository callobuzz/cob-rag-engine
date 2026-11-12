import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { useErrorText } from "@/features/insights/ui";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { Panel } from "@/features/kbs/ui";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { fill } from "@/lib/strings/en";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import {
	criteriaQuery,
	evalSettingsQuery,
	useCreateCriterion,
	useDeleteCriterion,
	useSaveEvalSettings,
	useUpdateCriterion,
} from "../evals-api";
import { metricLabel } from "../runs/format";

const c = t.criteria;

const criterionSchema = z.object({
	name: z
		.string()
		.trim()
		.regex(/^[a-z0-9][a-z0-9-]*$/, c.nameInvalid)
		.max(60, c.nameInvalid),
	definition: z.string().trim().min(1, c.definitionRequired).max(500),
	threshold: z.coerce.number().min(0).max(1),
});
type CriterionIn = z.input<typeof criterionSchema>;
type CriterionOut = z.output<typeof criterionSchema>;

const judgeSchema = z.object({
	judge_provider: z.string().trim().max(64),
	judge_model: z.string().trim().max(200),
	judge_api_key: z.string().max(500),
	judge_base_url: z.string().trim().max(500),
});
type JudgeForm = z.infer<typeof judgeSchema>;

type ThresholdForm = Record<string, string>;

function CriterionForm({ kb }: { kb: string }) {
	const create = useCreateCriterion(kb);
	const ids = { name: useId(), def: useId(), thr: useId() };
	const form = useAppForm<CriterionIn, unknown, CriterionOut>({
		resolver: zodResolver(criterionSchema),
		defaultValues: { name: "", definition: "", threshold: 0.7 },
	});
	const submit = useAppSubmit(form, async (v) => {
		await create.mutateAsync({ ...v, enabled: true });
		toast.success(c.added);
		form.reset();
	});
	const e = form.formState.errors;
	return (
		<form onSubmit={(ev) => void submit.onSubmit(ev)} noValidate className="grid gap-3 p-4">
			{submit.error ? <KbErrorAlert error={submit.error} /> : null}
			<Field id={ids.name} label={c.name} error={e.name?.message}>
				<Input
					id={ids.name}
					aria-invalid={!!e.name}
					aria-describedby={describedBy(ids.name, { error: !!e.name })}
					{...form.register("name")}
				/>
			</Field>
			<Field id={ids.def} label={c.definition} error={e.definition?.message}>
				<Textarea
					id={ids.def}
					rows={2}
					aria-invalid={!!e.definition}
					aria-describedby={describedBy(ids.def, { error: !!e.definition })}
					{...form.register("definition")}
				/>
			</Field>
			<Field id={ids.thr} label={c.threshold}>
				<Input id={ids.thr} type="number" min={0} max={1} step={0.05} {...form.register("threshold")} />
			</Field>
			<div>
				<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
					{c.add}
				</SubmitButton>
			</div>
		</form>
	);
}

function CriteriaList({ kb }: { kb: string }) {
	const q = useQuery(criteriaQuery(kb));
	const update = useUpdateCriterion(kb);
	const remove = useDeleteCriterion(kb);
	const errorText = useErrorText();
	if (q.isError) return <ErrorState error={q.error} title={c.error} onRetry={() => void q.refetch()} />;
	const items = q.data?.items ?? [];
	if (q.isPending) return null;
	if (items.length === 0) return <p className="m-0 px-4 pt-4 text-foreground-2">{c.empty}</p>;
	return (
		<ul className="m-0 grid list-none gap-2 p-4 pb-0">
			{items.map((x) => (
				<li
					key={x.id}
					className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-border p-3"
				>
					<div className="min-w-0">
						<strong>{x.name}</strong>
						<p className="m-0 text-foreground-2">{x.definition}</p>
						<small className="text-muted-foreground">{fill(c.passAt, { t: Math.round(x.threshold * 100) })}</small>
					</div>
					<div className="flex items-center gap-2">
						<Switch
							checked={x.enabled}
							aria-label={fill(x.enabled ? c.disable : c.enable, { name: x.name })}
							onCheckedChange={(v) =>
								update.mutate({ id: x.id, enabled: v }, { onError: (e) => toast.error(errorText(e)) })
							}
						/>
						<Button
							type="button"
							size="sm"
							variant="destructive"
							aria-label={fill(c.delete, { name: x.name })}
							onClick={() =>
								remove.mutate(x.id, {
									onSuccess: () => toast.success(c.deleted),
									onError: (e) => toast.error(errorText(e)),
								})
							}
						>
							{fill(c.delete, { name: "" }).trim()}
						</Button>
					</div>
				</li>
			))}
		</ul>
	);
}

function ThresholdsForm({ kb }: { kb: string }) {
	const settings = useQuery(evalSettingsQuery(kb));
	const save = useSaveEvalSettings(kb);
	const s = settings.data;
	const form = useAppForm<ThresholdForm>({
		values: Object.fromEntries(
			Object.keys(s?.effective_thresholds ?? {}).map((m) => [m, String(s?.thresholds[m] ?? "")]),
		),
	});
	const submit = useAppSubmit(form, async (v) => {
		const thresholds: Record<string, number> = {};
		for (const [m, raw] of Object.entries(v)) if (raw.trim() !== "") thresholds[m] = Number(raw);
		await save.mutateAsync({
			judge_provider: s?.judge_provider ?? null,
			judge_model: s?.judge_model ?? null,
			judge_base_url: s?.judge_base_url ?? null,
			judge_api_key: null,
			thresholds,
		});
		toast.success(c.saved);
	});
	if (!s) return null;
	return (
		<form onSubmit={(ev) => void submit.onSubmit(ev)} noValidate className="grid gap-3 p-4">
			{submit.error ? <KbErrorAlert error={submit.error} /> : null}
			<div className="grid gap-3 min-[861px]:grid-cols-3">
				{Object.entries(s.effective_thresholds).map(([m, def]) => (
					<Field key={m} id={`thr-${m}`} label={metricLabel(m)}>
						<Input
							id={`thr-${m}`}
							type="number"
							min={0}
							max={1}
							step={0.05}
							placeholder={String(def)}
							{...form.register(m)}
						/>
					</Field>
				))}
			</div>
			<div>
				<OwnerGate action="eval.settings">
					<SubmitButton pending={submit.isSubmitting} locked={submit.locked}>
						{c.saveThresholds}
					</SubmitButton>
				</OwnerGate>
			</div>
		</form>
	);
}

function JudgeForm({ kb }: { kb: string }) {
	const settings = useQuery(evalSettingsQuery(kb));
	const save = useSaveEvalSettings(kb);
	const ids = { prov: useId(), model: useId(), key: useId(), url: useId() };
	const s = settings.data;
	const form = useAppForm<JudgeForm>({
		resolver: zodResolver(judgeSchema),
		values: {
			judge_provider: s?.judge_provider ?? "",
			judge_model: s?.judge_model ?? "",
			judge_api_key: "",
			judge_base_url: s?.judge_base_url ?? "",
		},
	});
	const submit = useAppSubmit(form, async (v) => {
		await save.mutateAsync({
			judge_provider: v.judge_provider || null,
			judge_model: v.judge_model || null,
			// null keeps the stored key; the form never shows it.
			judge_api_key: v.judge_api_key || null,
			judge_base_url: v.judge_base_url || null,
			thresholds: s?.thresholds ?? {},
		});
		form.setValue("judge_api_key", "");
		toast.success(c.saved);
	});
	return (
		<form onSubmit={(ev) => void submit.onSubmit(ev)} noValidate className="grid gap-3 p-4">
			{submit.error ? <KbErrorAlert error={submit.error} /> : null}
			<Field id={ids.prov} label={c.judgeProvider}>
				<Input id={ids.prov} {...form.register("judge_provider")} />
			</Field>
			<Field id={ids.model} label={c.judgeModel}>
				<Input id={ids.model} {...form.register("judge_model")} />
			</Field>
			<Field id={ids.key} label={c.judgeKey} hint={s?.judge_has_key ? c.judgeKeyStored : undefined}>
				<Input
					id={ids.key}
					type="password"
					autoComplete="off"
					aria-describedby={describedBy(ids.key, { hint: !!s?.judge_has_key })}
					{...form.register("judge_api_key")}
				/>
			</Field>
			<Field id={ids.url} label={c.judgeUrl}>
				<Input id={ids.url} {...form.register("judge_base_url")} />
			</Field>
			<div>
				<OwnerGate action="eval.settings">
					<SubmitButton pending={submit.isSubmitting} locked={submit.locked}>
						{c.saveJudge}
					</SubmitButton>
				</OwnerGate>
			</div>
		</form>
	);
}

/** Custom criteria, pass marks and the judge model of one knowledge base. */
export function CriteriaTab({ kb }: { kb: string }) {
	return (
		<div className="grid gap-4">
			<Panel title={c.title} sub={c.sub}>
				<CriteriaList kb={kb} />
				<CriterionForm kb={kb} />
			</Panel>
			<Panel title={c.thresholdsTitle} sub={c.thresholdsSub}>
				<ThresholdsForm kb={kb} />
			</Panel>
			<Panel title={c.judgeTitle} sub={c.judgeSub}>
				<JudgeForm kb={kb} />
			</Panel>
		</div>
	);
}
