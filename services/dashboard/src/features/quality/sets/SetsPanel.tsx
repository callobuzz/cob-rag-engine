import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { FilePlus2, Plus } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useErrorText } from "@/features/insights/ui";
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { Panel } from "@/features/kbs/ui";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { fill } from "@/lib/strings/en";
import { qualityEvalsStrings as t } from "@/lib/strings/quality-evals";
import { type GoldenSet, goldenSetsQuery, useCreateGoldenSet, useDeleteGoldenSet, useFromTemplate } from "../evals-api";

const s = t.sets;
const nameSchema = z.object({ name: z.string().trim().min(1, s.nameRequired).max(120) });
type NameForm = z.infer<typeof nameSchema>;

function NewSetDialog({ kb, onClose }: { kb: string; onClose: () => void }) {
	const id = useId();
	const create = useCreateGoldenSet(kb);
	const form = useAppForm<NameForm>({ resolver: zodResolver(nameSchema), defaultValues: { name: "" } });
	const submit = useAppSubmit(form, async (v) => {
		await create.mutateAsync({ name: v.name });
		toast.success(s.created);
		onClose();
	});
	const err = form.formState.errors.name?.message;
	return (
		<Dialog open onOpenChange={(o) => (o ? null : onClose())}>
			<DialogContent className="sm:max-w-[440px]">
				<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
					<DialogHeader>
						<DialogTitle>{s.newTitle}</DialogTitle>
						<DialogDescription className="sr-only">{s.newTitle}</DialogDescription>
					</DialogHeader>
					{submit.error ? <KbErrorAlert error={submit.error} /> : null}
					<Field id={id} label={s.nameLabel} error={err}>
						<Input
							id={id}
							aria-invalid={!!err}
							aria-describedby={describedBy(id, { error: !!err })}
							{...form.register("name")}
						/>
					</Field>
					<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
						{s.create}
					</SubmitButton>
				</form>
			</DialogContent>
		</Dialog>
	);
}

/**
 * The golden sets of a knowledge base: what each holds and how its last run went. Making a set,
 * adding the template's tests and deleting a set (owners only; the default set stays) live here;
 * questions are added and edited in the table below.
 */
export function SetsPanel({ kb }: { kb: string }) {
	const q = useQuery(goldenSetsQuery(kb));
	const remove = useDeleteGoldenSet(kb);
	const template = useFromTemplate(kb);
	const errorText = useErrorText();
	const [creating, setCreating] = useState(false);
	const [deleting, setDeleting] = useState<GoldenSet | null>(null);

	const columns: Column<GoldenSet>[] = [
		{
			id: "name",
			header: s.colName,
			rowHeader: true,
			cell: (x) => (
				<span className="font-semibold">
					{x.name} {x.is_default ? <Badge variant="outline">{s.default}</Badge> : null}
					<span className="block text-[12.5px] font-normal text-muted-foreground">{s.sources[x.source]}</span>
				</span>
			),
		},
		{
			id: "size",
			header: s.colSize,
			stackLabel: true,
			cell: (x) => fill(s.sizeLine, { n: x.pair_count, b: x.behaviour_count }),
		},
		{
			id: "last",
			header: s.colLast,
			stackLabel: true,
			cell: (x) => (x.last_run ? fill(s.lastLine, { passed: x.last_run.passed, total: x.last_run.total }) : s.noRun),
		},
		{
			id: "actions",
			header: "",
			cell: (x) =>
				x.is_default ? null : (
					<OwnerGate action="golden.delete">
						<Button
							size="sm"
							variant="destructive"
							aria-label={fill(s.deleteFor, { name: x.name })}
							onClick={() => setDeleting(x)}
						>
							{s.delete}
						</Button>
					</OwnerGate>
				),
		},
	];

	const actions = (
		<div className="flex flex-wrap gap-2">
			<Button type="button" onClick={() => setCreating(true)}>
				<Plus aria-hidden />
				{s.newSet}
			</Button>
			<Button
				type="button"
				disabled={template.isPending}
				onClick={() =>
					template.mutate(undefined, {
						onSuccess: () => toast.success(s.templateAdded),
						onError: (e) => toast.error(errorText(e)),
					})
				}
			>
				<FilePlus2 aria-hidden />
				{s.fromTemplate}
			</Button>
		</div>
	);

	return (
		<>
			<Panel title={s.title} sub={s.sub} aside={actions}>
				{q.isError ? (
					<div className="p-4">
						<ErrorState error={q.error} title={s.error} onRetry={() => void q.refetch()} />
					</div>
				) : q.isPending ? (
					<div className="p-4">
						<TableSkeleton rows={2} />
					</div>
				) : (
					<div className="px-2">
						<DataTable columns={columns} rows={q.data.items} rowKey={(x) => x.id} caption={s.title} />
					</div>
				)}
			</Panel>
			{creating ? <NewSetDialog kb={kb} onClose={() => setCreating(false)} /> : null}
			<ConfirmDialog
				open={!!deleting}
				onOpenChange={(o) => !o && setDeleting(null)}
				title={s.deleteTitle}
				body={fill(s.deleteBody, { name: deleting?.name ?? "", n: deleting?.pair_count ?? 0 })}
				confirmLabel={s.delete}
				pending={remove.isPending}
				onConfirm={() =>
					deleting &&
					remove.mutate(deleting.id, {
						onSuccess: () => {
							toast.success(s.deleted);
							setDeleting(null);
						},
						onError: (e) => {
							setDeleting(null);
							toast.error(errorText(e));
						},
					})
				}
			/>
		</>
	);
}
