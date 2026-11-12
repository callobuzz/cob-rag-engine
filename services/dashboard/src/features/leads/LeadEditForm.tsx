import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useId } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Textarea } from "@/components/ui/textarea";
import { messageFor } from "@/lib/api/error-messages";
import { usePrincipal } from "@/lib/auth/store";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { LEAD_STATUSES } from "@/lib/search";
import { leadsStrings as t } from "@/lib/strings/leads";
import { teamQuery } from "../team/api";
import { type LeadDetail, type LeadUpdate, useUpdateLead } from "./api";

const NOTES_MAX = 5000;

const editSchema = z.object({
	status: z.enum(LEAD_STATUSES),
	assignee: z.string(),
	notes: z.string().max(NOTES_MAX, t.edit.notesTooLong),
});
type EditValues = z.infer<typeof editSchema>;

/**
 * Status, who follows up, and notes. Only what changed is sent, so saving a note never overwrites an
 * assignee someone else set a moment ago. Members work leads too; only delete and export are owners'.
 */
export function LeadEditForm({ lead }: { lead: LeadDetail }) {
	const ids = { status: useId(), assignee: useId(), notes: useId() };
	const principal = usePrincipal();
	// Teammates of the signed-in workspace. The operator's view has no single workspace to pick from.
	const team = useQuery({ ...teamQuery({ kind: "me" }), enabled: principal?.view === "tenant" });
	const update = useUpdateLead(lead.id);
	const form = useAppForm<EditValues>({
		resolver: zodResolver(editSchema),
		defaultValues: {
			status: lead.status,
			assignee: lead.assignee_user_id ?? "",
			notes: lead.notes ?? "",
		},
	});
	const errs = form.formState.errors;
	const dirty = form.formState.dirtyFields;

	const submit = useAppSubmit(
		form,
		async (v) => {
			const body: LeadUpdate = {};
			if (dirty.status) body.status = v.status;
			if (dirty.assignee) body.assignee_user_id = v.assignee || null;
			if (dirty.notes) body.notes = v.notes.trim() || null;
			if (Object.keys(body).length === 0) return;
			const saved = await update.mutateAsync(body);
			toast.success(t.edit.saved);
			form.reset({
				status: saved.status,
				assignee: saved.assignee_user_id ?? "",
				notes: saved.notes ?? "",
			});
		},
		{ fieldName: (path) => (path === "assignee_user_id" ? "assignee" : path === "notes" ? "notes" : null) },
	);

	const members = team.data?.members ?? [];
	const knowsTeam = principal?.view === "tenant";

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-3.5" aria-label={t.edit.title}>
			<h3 className="text-[13px] font-semibold">{t.edit.title}</h3>
			{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
			<div className="grid gap-3.5 min-[520px]:grid-cols-2">
				<Field id={ids.status} label={t.edit.status}>
					<NativeSelect id={ids.status} {...form.register("status")}>
						{LEAD_STATUSES.map((s) => (
							<option key={s} value={s}>
								{t.status[s]}
							</option>
						))}
					</NativeSelect>
				</Field>
				{knowsTeam ? (
					<Field id={ids.assignee} label={t.edit.assignee} error={errs.assignee?.message}>
						<NativeSelect
							id={ids.assignee}
							aria-invalid={!!errs.assignee || undefined}
							aria-describedby={describedBy(ids.assignee, { error: !!errs.assignee })}
							{...form.register("assignee")}
						>
							<option value="">{t.edit.unassigned}</option>
							{members.map((m) => (
								<option key={m.user_id} value={m.user_id}>
									{m.name}
								</option>
							))}
						</NativeSelect>
					</Field>
				) : null}
			</div>
			<Field id={ids.notes} label={t.edit.notes} hint={t.edit.notesHint} error={errs.notes?.message}>
				<Textarea
					id={ids.notes}
					rows={3}
					maxLength={NOTES_MAX}
					aria-invalid={!!errs.notes || undefined}
					aria-describedby={describedBy(ids.notes, { hint: true, error: !!errs.notes })}
					{...form.register("notes")}
				/>
			</Field>
			<div>
				<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
					{t.edit.save}
				</SubmitButton>
			</div>
		</form>
	);
}
