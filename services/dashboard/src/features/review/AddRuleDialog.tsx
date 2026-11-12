import { zodResolver } from "@hookform/resolvers/zod";
import { useId } from "react";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { reviewStrings as t } from "@/lib/strings/review";
import { type ReviewItem, useReviewAction } from "./api";

const d = t.ruleDialog;
const schema = z.object({ instruction: z.string().trim().min(1, d.instructionRequired).max(500) });
type Form = z.infer<typeof schema>;

/** A guidance rule for the draft. */
export function AddRuleDialog({
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
	const id = useId();
	const form = useAppForm<Form>({ resolver: zodResolver(schema), defaultValues: { instruction: "" } });
	const err = form.formState.errors.instruction;
	const submit = useAppSubmit(form, async (v) => {
		await act.mutateAsync({
			item_key: item.item_key,
			action: "added_rule",
			base_revision: revision,
			rule: { instruction: v.instruction, type: "context" },
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
					<Field id={id} label={d.instruction} error={err?.message}>
						<Textarea
							id={id}
							rows={3}
							aria-invalid={!!err}
							aria-describedby={describedBy(id, { error: !!err })}
							{...form.register("instruction")}
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
