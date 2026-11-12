import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useId } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { useErrorText } from "@/features/insights/ui";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { qualityStrings } from "@/lib/strings/insights";
import { qualityEvalsStrings as evalsT } from "@/lib/strings/quality-evals";
import { type GoldenPair, useCreateGolden, useUpdateGolden } from "./api";
import type { GoldenSet } from "./evals-api";
import { splitChunkIds } from "./golden-csv";
import { PairBehaviourFields } from "./sets/PairBehaviourFields";
import { type PairForm, pairSchema, splitPhrases } from "./sets/pair-form";

const t = qualityStrings.pairDialog;

export { ANSWER_MAX, QUESTION_MAX } from "./sets/pair-form";

/**
 * Add or edit a golden question: the question, an expected answer and/or an expected behaviour
 * (at least one), optional passage ids, "must mention" phrases, a citation, tags and the set it
 * belongs to. `initial` pre-fills a new pair (from a question in Insights).
 */
export function GoldenPairDialog({
	open,
	onOpenChange,
	kbId,
	kbName,
	pair,
	initial,
	sets,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	kbId: string;
	kbName: string;
	/** null = a new pair. */
	pair: GoldenPair | null;
	initial?: { query: string; expected_answer: string };
	/** The knowledge base's sets, for the "Set" choice of a new question. */
	sets?: GoldenSet[];
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[600px]">
				{open ? (
					<PairFormBody
						kbId={kbId}
						kbName={kbName}
						pair={pair}
						initial={initial}
						sets={sets}
						onDone={() => onOpenChange(false)}
					/>
				) : null}
			</DialogContent>
		</Dialog>
	);
}

function PairFormBody({
	kbId,
	kbName,
	pair,
	initial,
	sets,
	onDone,
}: {
	kbId: string;
	kbName: string;
	pair: GoldenPair | null;
	initial?: { query: string; expected_answer: string };
	sets?: GoldenSet[];
	onDone: () => void;
}) {
	const ids = { query: useId(), answer: useId(), chunks: useId(), set: useId() };
	const create = useCreateGolden();
	const update = useUpdateGolden();
	const errorText = useErrorText();
	const defaultSetId = sets?.find((x) => x.is_default)?.id;
	const start: PairForm = {
		query: pair?.query ?? initial?.query ?? "",
		expected_answer: pair?.expected_answer ?? initial?.expected_answer ?? "",
		chunks: (pair?.expected_chunk_ids ?? []).join("\n"),
		set_id: pair?.set_id ?? defaultSetId ?? "",
		behaviour: (pair?.expected_behaviour as PairForm["behaviour"] | null | undefined) ?? "",
		must_include: (pair?.must_include ?? []).join("\n"),
		must_cite: pair?.must_cite ?? "",
		difficulty: (pair?.difficulty as PairForm["difficulty"] | null | undefined) ?? "",
		topic: pair?.topic ?? "",
	};
	const form = useAppForm<PairForm>({ resolver: zodResolver(pairSchema), defaultValues: start });
	const errs = form.formState.errors;
	useEffect(() => {
		form.setFocus("query");
	}, [form]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const chunkIds = splitChunkIds(v.chunks);
			const phrases = splitPhrases(v.must_include);
			const base = {
				query: v.query.trim(),
				expected_chunk_ids: chunkIds.length ? chunkIds : null,
			};
			if (pair) {
				// Only what changed (or the always-sent trio), so an old question keeps its other fields.
				const body: Record<string, unknown> = {
					...base,
					expected_answer: v.expected_answer ? v.expected_answer.trim() : null,
				};
				if (v.behaviour !== start.behaviour) body.expected_behaviour = v.behaviour || null;
				if (v.must_include !== start.must_include) body.must_include = phrases;
				if (v.must_cite !== start.must_cite) body.must_cite = v.must_cite || null;
				if (v.difficulty !== start.difficulty) body.difficulty = v.difficulty || null;
				if (v.topic !== start.topic) body.topic = v.topic || null;
				if (!v.expected_answer && !pair.expected_answer) delete body.expected_answer;
				await update.mutateAsync({ id: pair.id, body });
				toast.success(t.saved);
			} else {
				await create.mutateAsync({
					corpus_id: kbId,
					origin: "human",
					...base,
					...(v.expected_answer ? { expected_answer: v.expected_answer.trim() } : {}),
					...(v.set_id && v.set_id !== defaultSetId ? { set_id: v.set_id } : {}),
					...(v.behaviour ? { expected_behaviour: v.behaviour } : {}),
					...(phrases.length ? { must_include: phrases } : {}),
					...(v.must_cite ? { must_cite: v.must_cite } : {}),
					...(v.difficulty ? { difficulty: v.difficulty } : {}),
					...(v.topic ? { topic: v.topic } : {}),
				});
				toast.success(t.created);
			}
			onDone();
		},
		{
			fieldName: (path) => {
				const key = path.split(".")[0];
				const field = key === "expected_chunk_ids" ? "chunks" : key === "expected_behaviour" ? "behaviour" : key;
				return field && field in start ? (field as keyof PairForm) : null;
			},
		},
	);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{pair ? t.editTitle : t.createTitle}</DialogTitle>
				<DialogDescription>{fill(t.lead, { kb: kbName })}</DialogDescription>
			</DialogHeader>
			{submit.error ? <FormAlert>{errorText(submit.error)}</FormAlert> : null}
			<Field id={ids.query} label={t.question} error={errs.query?.message}>
				<Textarea
					id={ids.query}
					rows={2}
					aria-invalid={!!errs.query}
					aria-describedby={describedBy(ids.query, { error: !!errs.query })}
					{...form.register("query")}
				/>
			</Field>
			<Field
				id={ids.answer}
				label={t.answer}
				hint={evalsT.fields.expectationHint}
				error={errs.expected_answer?.message}
			>
				<Textarea
					id={ids.answer}
					rows={5}
					aria-invalid={!!errs.expected_answer}
					aria-describedby={describedBy(ids.answer, { hint: true, error: !!errs.expected_answer })}
					{...form.register("expected_answer")}
				/>
			</Field>
			<PairBehaviourFields form={form} />
			{!pair && sets && sets.length > 1 ? (
				<Field id={ids.set} label={evalsT.pairs.setLabel}>
					<NativeSelect id={ids.set} {...form.register("set_id")}>
						{sets.map((x) => (
							<option key={x.id} value={x.id}>
								{x.name}
							</option>
						))}
					</NativeSelect>
				</Field>
			) : null}
			<Field id={ids.chunks} label={t.chunks} hint={t.chunksHint} error={errs.chunks?.message}>
				<Textarea
					id={ids.chunks}
					rows={2}
					className="font-mono text-[12.5px]"
					aria-invalid={!!errs.chunks}
					aria-describedby={describedBy(ids.chunks, { hint: true, error: !!errs.chunks })}
					{...form.register("chunks")}
				/>
			</Field>
			<DialogFooter>
				<Button type="button" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
					{pair ? t.save : t.create}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
