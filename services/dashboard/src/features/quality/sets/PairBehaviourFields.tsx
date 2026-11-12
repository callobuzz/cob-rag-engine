import { useId } from "react";
import type { UseFormReturn } from "react-hook-form";
import { describedBy, Field } from "@/components/auth/Field";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import { BEHAVIOURS, DIFFICULTIES, type PairForm } from "./pair-form";

const f = t.fields;

/** The behaviour half of a golden question: what the assistant should do, and how to tag it. */
export function PairBehaviourFields({ form }: { form: UseFormReturn<PairForm> }) {
	const ids = { behaviour: useId(), include: useId(), cite: useId(), difficulty: useId(), topic: useId() };
	const errs = form.formState.errors;
	return (
		<>
			<Field id={ids.behaviour} label={f.behaviour}>
				<NativeSelect id={ids.behaviour} {...form.register("behaviour", { deps: ["expected_answer"] })}>
					<option value="">{f.behaviourNone}</option>
					{BEHAVIOURS.map((b) => (
						<option key={b} value={b}>
							{t.behaviours[b]}
						</option>
					))}
				</NativeSelect>
			</Field>
			<Field id={ids.include} label={f.mustInclude} hint={f.mustIncludeHint} error={errs.must_include?.message}>
				<Textarea
					id={ids.include}
					rows={2}
					aria-invalid={!!errs.must_include}
					aria-describedby={describedBy(ids.include, { hint: true, error: !!errs.must_include })}
					{...form.register("must_include")}
				/>
			</Field>
			<Field id={ids.cite} label={f.mustCite} hint={f.mustCiteHint} error={errs.must_cite?.message}>
				<Input
					id={ids.cite}
					aria-invalid={!!errs.must_cite}
					aria-describedby={describedBy(ids.cite, { hint: true, error: !!errs.must_cite })}
					{...form.register("must_cite")}
				/>
			</Field>
			<div className="grid gap-3 min-[600px]:grid-cols-2">
				<Field id={ids.difficulty} label={f.difficulty}>
					<NativeSelect id={ids.difficulty} {...form.register("difficulty")}>
						<option value="">{f.difficultyNone}</option>
						{DIFFICULTIES.map((d) => (
							<option key={d} value={d}>
								{t.difficulty[d]}
							</option>
						))}
					</NativeSelect>
				</Field>
				<Field id={ids.topic} label={f.topic} error={errs.topic?.message}>
					<Input
						id={ids.topic}
						aria-invalid={!!errs.topic}
						aria-describedby={describedBy(ids.topic, { error: !!errs.topic })}
						{...form.register("topic")}
					/>
				</Field>
			</div>
		</>
	);
}
