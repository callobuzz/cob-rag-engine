import { zodResolver } from "@hookform/resolvers/zod";
import { useId } from "react";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { reviewStrings as t } from "@/lib/strings/review";
import { type ReviewItem, useReviewAction } from "./api";

const d = t.answerDialog;
const schema = z.object({
	topic_name: z.string().trim().min(1, d.topicRequired).max(60),
	answer: z.string().trim().min(1, d.answerRequired).max(2000),
});
type Form = z.infer<typeof schema>;

/** An exact answer for the group's question: becomes a topic with a word-for-word answer in the draft. */
export function AddAnswerDialog({
	kb,
	item,
	revision,
	onClose,
	onAdded,
}: {
	kb: string;
	item: ReviewItem;
	revision: number;
	onClose: () => void;
	onAdded: () => void;
}) {
	const act = useReviewAction(kb);
	const ids = { topic: useId(), answer: useId() };
	const form = useAppForm<Form>({ resolver: zodResolver(schema), defaultValues: { topic_name: "", answer: "" } });
	const errs = form.formState.errors;
	const submit = useAppSubmit(form, async (v) => {
		await act.mutateAsync({
			item_key: item.item_key,
			action: "added_answer",
			base_revision: revision,
			answer: {
				topic_name: v.topic_name,
				question: (item.examples[0]?.question ?? item.title).slice(0, 200),
				answer: v.answer,
				language: "en",
			},
		});
		onAdded();
		onClose();
	});
	return (
		<Dialog open onOpenChange={(o) => (o ? null : onClose())}>
			<DialogContent>
				<DialogHeader>
					<DialogTitle>{d.title}</DialogTitle>
					<DialogDescription>{d.lead}</DialogDescription>
				</DialogHeader>
				<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-3">
					{submit.error ? <KbErrorAlert error={submit.error} /> : null}
					<Field id={ids.topic} label={d.topic} error={errs.topic_name?.message}>
						<Input
							id={ids.topic}
							aria-invalid={!!errs.topic_name}
							aria-describedby={describedBy(ids.topic, { error: !!errs.topic_name })}
							{...form.register("topic_name")}
						/>
					</Field>
					<Field id={ids.answer} label={d.answer} error={errs.answer?.message}>
						<Textarea
							id={ids.answer}
							rows={4}
							aria-invalid={!!errs.answer}
							aria-describedby={describedBy(ids.answer, { error: !!errs.answer })}
							{...form.register("answer")}
						/>
					</Field>
					<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
						{d.submit}
					</SubmitButton>
				</form>
			</DialogContent>
		</Dialog>
	);
}
