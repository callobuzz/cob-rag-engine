import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useEffect, useId } from "react";
import { toast } from "sonner";
import { z } from "zod";
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
import { Input } from "@/components/ui/input";
import type { Corpus } from "@/lib/api/queries";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { portalStrings as t } from "@/lib/strings/portal";
import { pk, portal } from "./api";
import { portalMessage } from "./messages";

export const newWorkspaceSchema = z.object({
	title: z.string().trim().min(1, t.create.nameRequired).max(500),
	workflow_mode: z.enum(["single_reviewer", "maker_checker"]),
	assigned_reviewer: z.string().trim().max(255),
});
type Form = z.infer<typeof newWorkspaceSchema>;

/**
 * New workspace (title, workflow mode, assigned reviewer) → `POST /portal/workspaces?corpus_id=`
 * (always sent, A0). The creator is recorded by the engine (sessions send no `user`).
 */
export function NewWorkspaceDialog({
	open,
	onOpenChange,
	kb,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	kb: Corpus;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-lg">
				{open ? <NewWorkspaceForm kb={kb} onDone={() => onOpenChange(false)} /> : null}
			</DialogContent>
		</Dialog>
	);
}

function NewWorkspaceForm({ kb, onDone }: { kb: Corpus; onDone: () => void }) {
	const qc = useQueryClient();
	const navigate = useNavigate();
	const ids = { title: useId(), mode: useId(), reviewer: useId() };
	const form = useAppForm<Form>({
		resolver: zodResolver(newWorkspaceSchema),
		defaultValues: { title: "", workflow_mode: "single_reviewer", assigned_reviewer: "" },
	});
	const errs = form.formState.errors;
	useEffect(() => {
		form.setFocus("title");
	}, [form]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const w = await portal.create(kb.id, {
				title: v.title,
				workflow_mode: v.workflow_mode,
				assigned_reviewer: v.assigned_reviewer || null,
			});
			await qc.invalidateQueries({ queryKey: pk.lists });
			toast.success(t.create.created, { description: w.title });
			onDone();
			void navigate({ to: "/portal/$workspaceId", params: { workspaceId: w.id }, search: { kb: kb.id } });
		},
		{
			fieldName: (path) =>
				(["title", "workflow_mode", "assigned_reviewer"] as const).find((k) => path.startsWith(k)) ?? null,
		},
	);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{t.create.title}</DialogTitle>
				<DialogDescription>{fill(t.create.lead, { kb: kb.name })}</DialogDescription>
			</DialogHeader>
			{submit.error ? <FormAlert>{portalMessage(submit.error).title}</FormAlert> : null}
			<Field id={ids.title} label={t.create.name} error={errs.title?.message}>
				<Input
					id={ids.title}
					autoComplete="off"
					placeholder={t.create.namePlaceholder}
					aria-required="true"
					aria-invalid={!!errs.title}
					aria-describedby={describedBy(ids.title, { error: !!errs.title })}
					{...form.register("title")}
				/>
			</Field>
			<Field id={ids.mode} label={t.create.mode} hint={t.create.modeHint}>
				<NativeSelect
					id={ids.mode}
					aria-describedby={describedBy(ids.mode, { hint: true })}
					{...form.register("workflow_mode")}
				>
					<option value="single_reviewer">{t.mode.single_reviewer}</option>
					<option value="maker_checker">{t.mode.maker_checker}</option>
				</NativeSelect>
			</Field>
			<Field
				id={ids.reviewer}
				label={t.create.reviewer}
				hint={t.create.reviewerHint}
				error={errs.assigned_reviewer?.message}
			>
				<Input
					id={ids.reviewer}
					autoComplete="off"
					placeholder={t.create.reviewerPlaceholder}
					aria-invalid={!!errs.assigned_reviewer}
					aria-describedby={describedBy(ids.reviewer, { hint: true, error: !!errs.assigned_reviewer })}
					{...form.register("assigned_reviewer")}
				/>
			</Field>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.create.submitting}
				>
					{t.create.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
