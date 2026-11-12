import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useMemo } from "react";
import { useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Form2, FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { engineStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { type RetrievalDefaults, retrievalDefaultsQuery, useSaveRetrievalDefaults } from "./api";
import {
	checkField,
	DEFAULT_FIELDS,
	type DefaultKey,
	type DefaultValues,
	defaultsPatch,
	defaultValues,
	type FieldProblem,
} from "./retrieval-defaults";

const t = engineStrings.retrieval;

function problemText(p: FieldProblem): string {
	if (p.kind === "whole") return t.whole;
	if (p.kind === "above") return fill(t.above, { min: p.min });
	return fill(t.between, { min: p.min, max: p.max });
}

/**
 * Engine-wide retrieval defaults (`GET/PUT /admin/config/retrieval`): each field with a plain
 * explanation and its bounds; saves only the changed fields; `source` says whether they come from
 * .env or were changed here.
 */
export function RetrievalDefaultsForm() {
	const q = useQuery(retrievalDefaultsQuery);
	return (
		<Panel
			title={t.title}
			sub={t.sub}
			aside={
				q.data ? (
					<Badge variant={q.data.source === "overrides" ? "info" : "default"} data-testid="retrieval-source">
						{t.source[q.data.source] ?? q.data.source}
					</Badge>
				) : null
			}
		>
			{q.isPending ? (
				<TableSkeleton rows={4} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} onRetry={() => void q.refetch()} />
				</div>
			) : (
				<Editor data={q.data} />
			)}
		</Panel>
	);
}

const schema = z.object(
	Object.fromEntries(
		DEFAULT_FIELDS.map((f) => [
			f.key,
			z.string().superRefine((text, ctx) => {
				const p = checkField(f.key, text);
				if (p) ctx.addIssue({ code: "custom", message: problemText(p) });
			}),
		]),
	) as Record<DefaultKey, z.ZodString>,
);

function Editor({ data }: { data: RetrievalDefaults }) {
	const base = useId();
	const key = JSON.stringify(data);
	const initial = useMemo(() => defaultValues(JSON.parse(key) as RetrievalDefaults), [key]);
	const form = useAppForm<DefaultValues>({ resolver: zodResolver(schema), defaultValues: initial });
	const errs = form.formState.errors;
	const save = useSaveRetrievalDefaults();
	const { reset } = form;
	useEffect(() => reset(initial), [initial, reset]);
	const values = useWatch({ control: form.control }) as DefaultValues;
	const dirty = Object.keys(defaultsPatch(initial, values)).length > 0;

	const submit = useAppSubmit(
		form,
		async (v) => {
			const patch = defaultsPatch(initial, v);
			if (Object.keys(patch).length === 0) return;
			await save.mutateAsync(patch);
			toast.success(t.saved, { description: t.savedBody });
		},
		{ fieldName: (path) => (DEFAULT_FIELDS.some((d) => d.key === path) ? (path as DefaultKey) : null) },
	);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate aria-label={t.title}>
			<div className="grid gap-4 p-4">
				{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
				<Form2>
					{DEFAULT_FIELDS.map((f) => {
						const id = `${base}-${f.key}`;
						const meta = t.fields[f.key] ?? { label: f.key, help: "" };
						const bounds = f.max == null ? fill(t.above, { min: f.min }) : fill(t.between, { min: f.min, max: f.max });
						const err = errs[f.key]?.message;
						return (
							<Field key={f.key} id={id} label={meta.label} hint={`${meta.help} ${bounds}`} error={err}>
								<Input
									id={id}
									type="number"
									inputMode={f.integer ? "numeric" : "decimal"}
									min={f.min}
									max={f.max ?? undefined}
									step={f.step}
									className="font-mono"
									aria-invalid={!!err || undefined}
									aria-describedby={describedBy(id, { hint: true, error: !!err })}
									{...form.register(f.key)}
								/>
							</Field>
						);
					})}
				</Form2>
			</div>
			<FormFoot>
				<Button type="button" variant="ghost" disabled={!dirty || submit.isSubmitting} onClick={() => reset(initial)}>
					{t.discard}
				</Button>
				<SubmitButton
					variant="default"
					disabled={!dirty}
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.saving}
				>
					{t.save}
				</SubmitButton>
			</FormFoot>
		</form>
	);
}
