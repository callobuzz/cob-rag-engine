import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useId } from "react";
import { Controller } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { ErrorAlert } from "@/components/auth/ErrorAlert";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { SecretReveal } from "@/components/patterns/SecretReveal";
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
import { formatRelative } from "@/lib/format/date";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { keysStrings as t } from "@/lib/strings/keys";
import {
	type ApiKey,
	type CreatedKey,
	KEY_PERMISSIONS,
	type KeyOwner,
	type KeyPermission,
	useCreateKey,
	usedRecently,
	useUpdateKey,
} from "./api";
import { PermissionPicker, permissionRank } from "./PermissionPicker";

const keyFormSchema = z.object({
	name: z.string().trim().min(1, t.create.nameRequired).max(120, t.create.nameTooLong),
	permission: z.enum(KEY_PERMISSIONS as [KeyPermission, ...KeyPermission[]], t.errors.permission),
});
type KeyForm = z.infer<typeof keyFormSchema>;

/** "Create key": name and permission (read by default). The key itself comes back once. */
export function CreateKeyDialog({
	owner,
	open,
	onOpenChange,
	onCreated,
	defaultName = "",
	defaultPermission = "read",
}: {
	owner: KeyOwner;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onCreated: (key: CreatedKey) => void;
	defaultName?: string;
	defaultPermission?: KeyPermission;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-lg">
				{open ? (
					<CreateForm
						owner={owner}
						defaultName={defaultName}
						defaultPermission={defaultPermission}
						onCreated={onCreated}
						onCancel={() => onOpenChange(false)}
					/>
				) : null}
			</DialogContent>
		</Dialog>
	);
}

function CreateForm({
	owner,
	defaultName,
	defaultPermission,
	onCreated,
	onCancel,
}: {
	owner: KeyOwner;
	defaultName: string;
	defaultPermission: KeyPermission;
	onCreated: (key: CreatedKey) => void;
	onCancel: () => void;
}) {
	const nameId = useId();
	const create = useCreateKey(owner);
	const form = useAppForm<KeyForm>({
		resolver: zodResolver(keyFormSchema),
		defaultValues: { name: defaultName, permission: defaultPermission },
	});
	useEffect(() => {
		form.setFocus("name");
	}, [form]);
	const submit = useAppSubmit(form, async (v) => {
		const key = await create.mutateAsync({ name: v.name, permission: v.permission });
		toast.success(t.create.toast, { description: key.name });
		onCreated(key);
	});
	const err = form.formState.errors;
	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{t.create.title}</DialogTitle>
				<DialogDescription>{t.create.lead}</DialogDescription>
			</DialogHeader>
			{submit.error ? <ErrorAlert error={submit.error} until={submit.until} /> : null}
			<Field id={nameId} label={t.create.nameLabel} hint={t.create.nameHint} error={err.name?.message}>
				<Input
					id={nameId}
					autoComplete="off"
					placeholder={t.create.namePlaceholder}
					aria-invalid={!!err.name}
					aria-describedby={describedBy(nameId, { hint: true, error: !!err.name })}
					{...form.register("name")}
				/>
			</Field>
			<Controller
				control={form.control}
				name="permission"
				render={({ field }) => (
					<PermissionPicker value={field.value} onChange={field.onChange} invalid={!!err.permission} />
				)}
			/>
			<DialogFooter>
				<Button type="button" variant="outline" onClick={onCancel}>
					{en.common.cancel}
				</Button>
				<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
					{t.create.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}

/** "Edit key": rename it or change its permission; warns when it is in live use. */
export function EditKeyDialog({
	owner,
	keyRow,
	onOpenChange,
}: {
	owner: KeyOwner;
	keyRow: ApiKey | null;
	onOpenChange: (open: boolean) => void;
}) {
	return (
		<Dialog open={keyRow !== null} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-lg">
				{keyRow ? <EditForm owner={owner} keyRow={keyRow} onDone={() => onOpenChange(false)} /> : null}
			</DialogContent>
		</Dialog>
	);
}

function EditForm({ owner, keyRow, onDone }: { owner: KeyOwner; keyRow: ApiKey; onDone: () => void }) {
	const nameId = useId();
	const update = useUpdateKey(owner);
	const form = useAppForm<KeyForm>({
		resolver: zodResolver(keyFormSchema),
		defaultValues: { name: keyRow.name, permission: keyRow.permission },
	});
	const submit = useAppSubmit(form, async (v) => {
		const changed = v.name !== keyRow.name || v.permission !== keyRow.permission;
		if (changed) {
			await update.mutateAsync({
				id: keyRow.id,
				name: v.name !== keyRow.name ? v.name : undefined,
				permission: v.permission !== keyRow.permission ? v.permission : undefined,
			});
			toast.success(t.edit.toast, { description: v.name });
		}
		onDone();
	});
	const permission = form.watch("permission");
	const lowering = permissionRank(permission) < permissionRank(keyRow.permission);
	const live = usedRecently(keyRow);
	const err = form.formState.errors;
	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{fill(t.edit.title, { name: keyRow.name })}</DialogTitle>
				<DialogDescription>{t.edit.lead}</DialogDescription>
			</DialogHeader>
			{submit.error ? <ErrorAlert error={submit.error} until={submit.until} /> : null}
			<Field id={nameId} label={t.create.nameLabel} error={err.name?.message}>
				<Input
					id={nameId}
					autoComplete="off"
					aria-invalid={!!err.name}
					aria-describedby={describedBy(nameId, { error: !!err.name })}
					{...form.register("name")}
				/>
			</Field>
			<Controller
				control={form.control}
				name="permission"
				render={({ field }) => (
					<PermissionPicker value={field.value} onChange={field.onChange} invalid={!!err.permission} />
				)}
			/>
			{lowering && live ? (
				<FormAlert tone="warn">
					<span data-testid="edit-live-warning">
						{fill(t.edit.liveWarn, { name: keyRow.name, when: formatRelative(keyRow.last_used_at) })}{" "}
						{fill(t.edit.lowerWarn, { level: t.permission[permission].label.toLowerCase() })}
					</span>
				</FormAlert>
			) : lowering ? (
				<FormAlert tone="warn">
					<span data-testid="edit-lower-warning">
						{fill(t.edit.lowerWarn, { level: t.permission[permission].label.toLowerCase() })}
					</span>
				</FormAlert>
			) : null}
			<DialogFooter>
				<Button type="button" variant="outline" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton variant="default" pending={submit.isSubmitting} locked={submit.locked}>
					{t.edit.submit}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}

/** A key shown once (created or rotated). Closing forgets it; `selfSignOut` signs the caller out. */
export function RevealKeyDialog({
	title,
	keyValue,
	warning,
	selfSignOut = false,
	onClose,
}: {
	title: string | null;
	keyValue: string | null;
	warning?: string;
	selfSignOut?: boolean;
	onClose: () => void;
}) {
	return (
		<Dialog open={keyValue !== null} onOpenChange={(o) => (o ? undefined : onClose())}>
			<DialogContent className="sm:max-w-xl">
				<DialogHeader>
					<DialogTitle>{title ?? ""}</DialogTitle>
					<DialogDescription>{t.reveal.lead}</DialogDescription>
				</DialogHeader>
				{warning ? <FormAlert tone="warn">{warning}</FormAlert> : null}
				{selfSignOut ? <FormAlert tone="warn">{t.reveal.selfRotate}</FormAlert> : null}
				{keyValue ? <SecretReveal value={keyValue} label={t.reveal.label} /> : null}
				<DialogFooter>
					<Button variant="default" onClick={onClose}>
						{selfSignOut ? t.reveal.doneSignOut : t.reveal.done}
					</Button>
				</DialogFooter>
			</DialogContent>
		</Dialog>
	);
}
