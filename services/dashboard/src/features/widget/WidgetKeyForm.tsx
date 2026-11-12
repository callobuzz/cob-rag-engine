import { zodResolver } from "@hookform/resolvers/zod";
import { useEffect, useId } from "react";
import { Controller, type FieldPath } from "react-hook-form";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
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
import { KbErrorAlert } from "@/features/kbs/KbErrorAlert";
import { usePrincipal } from "@/lib/auth/store";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { en, fill } from "@/lib/strings/en";
import { widgetStrings as t } from "@/lib/strings/widget";
import { AppearanceFields } from "./AppearanceFields";
import { useCreateWidgetKey, useUpdateWidgetKey, type WidgetKey } from "./api";
import { MockBubble } from "./MockBubble";
import { OriginsInput } from "./OriginsInput";
import {
	type AppearanceForm,
	appearanceToBody,
	appearanceToForm,
	type CreateForm,
	type CreateFormInput,
	createSchema,
} from "./schemas";

/**
 * Create a widget key (name, sites, limits, appearance) or edit one (name, sites, limits; the
 * look is edited next to the preview). `widget_key_limit_reached` → form alert with "See plan";
 * 422 field errors land on their fields.
 */
export function WidgetKeyForm({
	corpusId,
	editing,
	open,
	onOpenChange,
	onCreated,
}: {
	corpusId: string;
	/** The key being edited; absent = create. */
	editing?: WidgetKey | null;
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onCreated?: (key: WidgetKey) => void;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-3xl">
				{open ? (
					<FormBody
						key={editing?.id ?? "new"}
						corpusId={corpusId}
						editing={editing ?? null}
						onDone={(k) => {
							onOpenChange(false);
							if (!editing) onCreated?.(k);
						}}
						onCancel={() => onOpenChange(false)}
					/>
				) : null}
			</DialogContent>
		</Dialog>
	);
}

const FIELD_NAMES = ["name", "allowed_origins", "rate_limit_per_minute", "daily_message_limit"] as const;
const APPEARANCE_NAMES = ["title", "greeting", "placeholder", "primary_color", "position", "show_sources"] as const;

/** A 422 `loc` path → the form field it belongs to. */
export function fieldForPath(path: string): FieldPath<CreateFormInput> | null {
	const [head, sub] = path.split(".");
	if (head === "appearance" && sub && (APPEARANCE_NAMES as readonly string[]).includes(sub)) {
		return `appearance.${sub as (typeof APPEARANCE_NAMES)[number]}`;
	}
	return head && (FIELD_NAMES as readonly string[]).includes(head) ? (head as (typeof FIELD_NAMES)[number]) : null;
}

function FormBody({
	corpusId,
	editing,
	onDone,
	onCancel,
}: {
	corpusId: string;
	editing: WidgetKey | null;
	onDone: (key: WidgetKey) => void;
	onCancel: () => void;
}) {
	const principal = usePrincipal();
	const base = useId();
	const ids = { name: `${base}-name`, sites: `${base}-sites`, rpm: `${base}-rpm`, daily: `${base}-daily` };
	const create = useCreateWidgetKey(corpusId);
	const update = useUpdateWidgetKey(corpusId);

	const form = useAppForm<CreateFormInput, unknown, CreateForm>({
		resolver: zodResolver(createSchema),
		defaultValues: {
			name: editing?.name ?? "",
			allowed_origins: editing?.allowed_origins ?? [],
			rate_limit_per_minute: editing?.rate_limit_per_minute ?? 10,
			daily_message_limit: editing?.daily_message_limit ?? 1000,
			appearance: appearanceToForm(editing?.appearance),
		},
	});
	const errs = form.formState.errors;
	useEffect(() => {
		form.setFocus("name");
	}, [form]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			const common = {
				name: v.name,
				allowed_origins: v.allowed_origins,
				rate_limit_per_minute: v.rate_limit_per_minute,
				daily_message_limit: v.daily_message_limit,
			};
			const key = editing
				? await update.mutateAsync({ id: editing.id, body: common })
				: await create.mutateAsync({ ...common, appearance: appearanceToBody(v.appearance, false) });
			if (editing) toast.success(fill(t.form.savedToast, { name: key.name }));
			else toast.success(t.form.createdToast, { description: fill(t.form.createdBody, { name: key.name }) });
			onDone(key);
		},
		{
			// 422 paths → fields; pydantic's "Value error, " prefix is noise for a person.
			onError: (err) => {
				let any = false;
				for (const f of err.fieldErrors) {
					const target = fieldForPath(f.path);
					if (!target) continue;
					form.setError(target, { type: "server", message: f.message.replace(/^Value error, /, "") });
					any = true;
				}
				return any;
			},
		},
	);

	const appearance = form.watch("appearance");
	const appearanceErrors = errs.appearance
		? Object.fromEntries(APPEARANCE_NAMES.map((n) => [n, errs.appearance?.[n]?.message]))
		: {};

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{editing ? fill(t.form.editTitle, { name: editing.name }) : t.form.createTitle}</DialogTitle>
				<DialogDescription>{editing ? t.form.editLead : t.form.createLead}</DialogDescription>
			</DialogHeader>
			{submit.error ? <KbErrorAlert error={submit.error} hideAction={principal?.view !== "tenant"} /> : null}
			<Field id={ids.name} label={t.form.name} hint={t.form.nameHint} error={errs.name?.message}>
				<Input
					id={ids.name}
					placeholder={t.form.namePlaceholder}
					autoComplete="off"
					aria-invalid={!!errs.name}
					aria-describedby={describedBy(ids.name, { hint: true, error: !!errs.name })}
					{...form.register("name")}
				/>
			</Field>
			<Field id={ids.sites} label={t.form.sites} hint={t.form.sitesHint} error={errs.allowed_origins?.message}>
				<Controller
					control={form.control}
					name="allowed_origins"
					render={({ field }) => (
						<OriginsInput
							id={ids.sites}
							value={field.value}
							onChange={field.onChange}
							onBlur={field.onBlur}
							inputRef={field.ref}
							error={errs.allowed_origins?.message}
							describedBy={describedBy(ids.sites, { hint: true, error: !!errs.allowed_origins })}
						/>
					)}
				/>
			</Field>
			<div className="grid gap-4 min-[600px]:grid-cols-2">
				<Field id={ids.rpm} label={t.form.rpm} hint={t.form.rpmHint} error={errs.rate_limit_per_minute?.message}>
					<Input
						id={ids.rpm}
						type="number"
						inputMode="numeric"
						min={1}
						max={600}
						aria-invalid={!!errs.rate_limit_per_minute}
						aria-describedby={describedBy(ids.rpm, { hint: true, error: !!errs.rate_limit_per_minute })}
						{...form.register("rate_limit_per_minute")}
					/>
				</Field>
				<Field id={ids.daily} label={t.form.daily} hint={t.form.dailyHint} error={errs.daily_message_limit?.message}>
					<Input
						id={ids.daily}
						type="number"
						inputMode="numeric"
						min={0}
						max={10_000_000}
						aria-invalid={!!errs.daily_message_limit}
						aria-describedby={describedBy(ids.daily, { hint: true, error: !!errs.daily_message_limit })}
						{...form.register("daily_message_limit")}
					/>
				</Field>
			</div>
			{editing ? null : (
				<section aria-label={t.form.appearance} className="grid gap-3 rounded-md border border-border p-3">
					<h3 className="text-[14px] font-bold">{t.form.appearance}</h3>
					<div className="grid items-start gap-4 min-[861px]:grid-cols-[minmax(0,1fr)_260px]">
						<Controller
							control={form.control}
							name="appearance"
							render={({ field }) => (
								<AppearanceFields
									value={field.value as AppearanceForm}
									errors={appearanceErrors}
									onChange={(patch) => field.onChange({ ...(field.value as AppearanceForm), ...patch })}
								/>
							)}
						/>
						<MockBubble value={appearance as AppearanceForm} />
					</div>
				</section>
			)}
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onCancel}>
					{en.common.cancel}
				</Button>
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={editing ? t.form.saving : t.form.creating}
				>
					{editing ? t.form.submitEdit : t.form.submitCreate}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}
