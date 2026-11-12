import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
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
import { Textarea } from "@/components/ui/textarea";
import { localLlmQuery } from "@/features/engine/local-llm-api"; // PIP4
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { en, fill } from "@/lib/strings/en";
import { localChoiceStrings, pipelinesStrings } from "@/lib/strings/pipelines";
import { type Profile, type ProfileResolved, useCreateProfile, useUpdateProfile } from "./api";
import {
	configFromForm,
	emptyForm,
	type FieldKey,
	fieldForServerDetail,
	formFromProfile,
	type ProfileForm,
	validateForm,
} from "./format";
import { ProfileFields } from "./ProfileFields";

const t = pipelinesStrings;

/**
 * Create or edit a pipeline profile: name and label, then the four plain sections. Mistakes are
 * caught here with the engine's own rules; a sentence the engine still refuses with
 * (`invalid_profile`) lands under the field it names.
 */
export function ProfileDialog({
	open,
	onOpenChange,
	profile,
	fallbackResolved,
}: {
	open: boolean;
	onOpenChange: (o: boolean) => void;
	/** Edit this profile; absent = create a new one. */
	profile?: Profile | null;
	/** What empty fields follow when there is no profile yet (the default profile's resolved block). */
	fallbackResolved?: ProfileResolved;
}) {
	const id = useId();
	const create = useCreateProfile();
	const update = useUpdateProfile();
	const [form, setForm] = useState<ProfileForm>(emptyForm());
	const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({});
	const [alert, setAlert] = useState<string | null>(null);
	const pending = create.isPending || update.isPending;
	// PIP4: a profile that answers on this server's CPU is only as good as the local model is ready
	const localStatus = useQuery({ ...localLlmQuery, enabled: open && form.route === "local" });
	const localNotReady = form.route === "local" && localStatus.data !== undefined && localStatus.data.state !== "ready";

	useEffect(() => {
		if (!open) return;
		setForm(profile ? formFromProfile(profile) : emptyForm());
		setErrors({});
		setAlert(null);
	}, [open, profile]);

	const set = (patch: Partial<ProfileForm>) => {
		setForm((f) => ({ ...f, ...patch }));
		// A field the person is fixing stops complaining at once.
		setErrors((e) => {
			if (Object.keys(e).length === 0) return e;
			return validateForm({ ...form, ...patch });
		});
	};

	const submit = async (ev: React.FormEvent) => {
		ev.preventDefault();
		setAlert(null);
		const found = validateForm(form);
		setErrors(found);
		if (Object.keys(found).length > 0) return;
		const body = {
			name: form.name.trim(),
			label: form.label.trim(),
			description: form.description.trim() === "" ? null : form.description.trim(),
			config: configFromForm(form) as unknown as Record<string, unknown>,
		};
		try {
			if (profile) {
				await update.mutateAsync({ id: profile.id, body });
				toast.success(fill(t.card.saved, { name: body.name }));
			} else {
				await create.mutateAsync(body);
				toast.success(fill(t.card.created, { name: body.name }));
			}
			if (localNotReady) toast.warning(fill(localChoiceStrings.notReadySaved, { name: body.name }));
			onOpenChange(false);
		} catch (e) {
			const err = toApiError(e);
			if (err.code === "profile_name_taken") setErrors({ name: messageFor(err).title });
			else if (err.code === "invalid_profile") {
				const field = fieldForServerDetail(err.detail);
				if (field) setErrors({ [field]: err.detail });
				else setAlert(err.detail);
			} else setAlert(messageFor(err).title);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[760px]">
				<form onSubmit={(e) => void submit(e)} noValidate className="grid gap-4">
					<DialogHeader>
						<DialogTitle>
							{profile ? fill(t.dialog.editTitle, { name: profile.name }) : t.dialog.createTitle}
						</DialogTitle>
						<DialogDescription>{profile ? t.dialog.lead : t.dialog.createLead}</DialogDescription>
					</DialogHeader>
					{alert ? <FormAlert>{alert}</FormAlert> : null}
					{localNotReady ? <FormAlert tone="warn">{localChoiceStrings.notReadyWarning}</FormAlert> : null}
					<div className="grid gap-3 sm:grid-cols-2">
						<Field id={`${id}-name`} label={t.dialog.name} hint={t.dialog.nameHint} error={errors.name}>
							<Input
								id={`${id}-name`}
								autoComplete="off"
								maxLength={80}
								aria-invalid={errors.name ? true : undefined}
								aria-describedby={describedBy(`${id}-name`, { hint: true, error: !!errors.name })}
								value={form.name}
								onChange={(e) => set({ name: e.target.value })}
							/>
						</Field>
						<Field id={`${id}-label`} label={t.dialog.label} hint={t.dialog.labelHint} error={errors.label}>
							<Input
								id={`${id}-label`}
								autoComplete="off"
								maxLength={60}
								aria-invalid={errors.label ? true : undefined}
								aria-describedby={describedBy(`${id}-label`, { hint: true, error: !!errors.label })}
								value={form.label}
								onChange={(e) => set({ label: e.target.value })}
							/>
						</Field>
					</div>
					<Field id={`${id}-desc`} label={t.dialog.description} hint={t.dialog.descriptionHint}>
						<Textarea
							id={`${id}-desc`}
							rows={2}
							aria-describedby={`${id}-desc-hint`}
							value={form.description}
							onChange={(e) => set({ description: e.target.value })}
						/>
					</Field>
					<ProfileFields form={form} set={set} errors={errors} resolved={profile?.resolved ?? fallbackResolved} />
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton
							variant="default"
							pending={pending}
							locked={pending}
							pendingLabel={profile ? t.dialog.saving : t.dialog.creating}
						>
							{profile ? t.dialog.save : t.dialog.create}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
