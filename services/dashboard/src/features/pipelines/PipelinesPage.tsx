import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
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
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { en, fill } from "@/lib/strings/en";
import { pipelinesStrings as t } from "@/lib/strings/pipelines";
import { type Profile, pipelinesQuery, useCloneProfile, useDeleteProfile, useMakeDefault } from "./api";
import { usedByText } from "./format";
import { type ProfileAction, ProfileCard } from "./ProfileCard";
import { ProfileDialog } from "./ProfileDialog";

function CloneDialog({ source, onOpenChange }: { source: Profile | null; onOpenChange: (o: boolean) => void }) {
	const id = useId();
	const clone = useCloneProfile();
	const [name, setName] = useState("");
	const [error, setError] = useState<string | null>(null);
	const [seen, setSeen] = useState<string | null>(null);
	if (source && seen !== source.id) {
		setSeen(source.id);
		setName("");
		setError(null);
	}
	const submit = async (ev: React.FormEvent) => {
		ev.preventDefault();
		if (!source) return;
		const n = name.trim();
		if (n === "") return setError(t.dialog.nameRequired);
		try {
			const copy = await clone.mutateAsync({ id: source.id, name: n });
			toast.success(fill(t.card.cloned, { name: copy.name }));
			setSeen(null);
			onOpenChange(false);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	return (
		<Dialog open={source !== null} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[480px]">
				<form onSubmit={(e) => void submit(e)} noValidate className="grid gap-4">
					<DialogHeader>
						<DialogTitle>{fill(t.clone.title, { name: source?.name ?? "" })}</DialogTitle>
						<DialogDescription>{t.clone.lead}</DialogDescription>
					</DialogHeader>
					<Field id={`${id}-name`} label={t.clone.name} error={error}>
						<Input
							id={`${id}-name`}
							autoComplete="off"
							maxLength={80}
							placeholder={fill(t.clone.nameDefault, { name: source?.name ?? "" })}
							aria-invalid={error ? true : undefined}
							value={name}
							onChange={(e) => {
								setName(e.target.value);
								setError(null);
							}}
						/>
					</Field>
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={clone.isPending} pendingLabel={t.clone.submitting}>
							{t.clone.submit}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}

/** `/admin/pipelines`: every pipeline profile, with create, clone, edit, make default and delete. */
export function PipelinesPage() {
	const q = useQuery(pipelinesQuery);
	const makeDefault = useMakeDefault();
	const del = useDeleteProfile();
	const [editing, setEditing] = useState<Profile | null>(null);
	const [creating, setCreating] = useState(false);
	const [cloning, setCloning] = useState<Profile | null>(null);
	const [deleting, setDeleting] = useState<Profile | null>(null);
	const [deleteError, setDeleteError] = useState<string | null>(null);

	const items = q.data?.items ?? [];
	const fallback = items.find((p) => p.is_default)?.resolved;

	const onAction = async (a: ProfileAction, p: Profile) => {
		if (a === "edit") setEditing(p);
		else if (a === "clone") setCloning(p);
		else if (a === "delete") {
			setDeleteError(null);
			setDeleting(p);
		} else {
			try {
				await makeDefault.mutateAsync(p.id);
				toast.success(fill(t.card.madeDefault, { name: p.name }));
			} catch (e) {
				toast.error(messageFor(toApiError(e)).title);
			}
		}
	};

	const doDelete = async () => {
		if (!deleting) return;
		setDeleteError(null);
		try {
			await del.mutateAsync(deleting.id);
			toast.success(fill(t.card.deleted, { name: deleting.name }));
			setDeleting(null);
		} catch (e) {
			const err = toApiError(e);
			if (err.code === "profile_in_use") {
				const plans = Number(err.extra.plans ?? 0);
				const tenants = Number(err.extra.tenants ?? 0);
				const targets = Number(err.extra.downgrade_targets ?? 0);
				const parts = [
					usedByText(plans, tenants) ? fill(t.delete.inUse, { parts: usedByText(plans, tenants) ?? "" }) : null,
					targets > 0 ? fill(t.delete.inUseDowngrade, { n: targets }) : null,
					t.delete.moveFirst,
				].filter(Boolean);
				setDeleteError(parts.join(" "));
			} else if (err.code === "profile_is_default") setDeleteError(t.delete.isDefault);
			else setDeleteError(messageFor(err).title);
		}
	};

	return (
		<div className="grid gap-4">
			<PageHeader
				title={t.page.title}
				description={t.page.lead}
				actions={
					<Button variant="default" onClick={() => setCreating(true)}>
						<Plus aria-hidden />
						{t.page.add}
					</Button>
				}
			/>
			{q.isPending ? (
				<CardSkeleton lines={4} />
			) : q.isError ? (
				<ErrorState error={q.error} title={t.page.error} onRetry={() => void q.refetch()} />
			) : items.length === 0 ? (
				<EmptyState
					title={t.page.emptyTitle}
					body={t.page.emptyBody}
					primaryAction={
						<Button variant="default" onClick={() => setCreating(true)}>
							<Plus aria-hidden />
							{t.page.add}
						</Button>
					}
				/>
			) : (
				<ul
					aria-label={t.page.listLabel}
					className="m-0 grid list-none gap-3 p-0 min-[861px]:grid-cols-2 min-[1280px]:grid-cols-3"
				>
					{items.map((p) => (
						<ProfileCard key={p.id} profile={p} onAction={(a, x) => void onAction(a, x)} />
					))}
				</ul>
			)}
			<ProfileDialog open={creating} onOpenChange={setCreating} fallbackResolved={fallback} />
			<ProfileDialog
				open={editing !== null}
				onOpenChange={(o) => (o ? undefined : setEditing(null))}
				profile={editing}
			/>
			<CloneDialog source={cloning} onOpenChange={(o) => (o ? undefined : setCloning(null))} />
			<ConfirmDialog
				open={deleting !== null}
				onOpenChange={(o) => (o ? undefined : setDeleting(null))}
				title={fill(t.delete.title, { name: deleting?.name ?? "" })}
				body={t.delete.body}
				confirmLabel={t.delete.confirm}
				pending={del.isPending}
				onConfirm={() => void doDelete()}
			>
				{deleteError ? <FormAlert>{deleteError}</FormAlert> : null}
			</ConfirmDialog>
		</div>
	);
}
