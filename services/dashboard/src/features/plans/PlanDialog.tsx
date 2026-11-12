import { zodResolver } from "@hookform/resolvers/zod";
import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Controller } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect"; // TV6
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
	Dialog,
	DialogContent,
	DialogDescription,
	DialogFooter,
	DialogHeader,
	DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { AnswerSpeedField, FairUseFields } from "@/features/fair-use/FairUseFields"; // PIP4
import { bodyFromDraft, type DraftErrors, draftFromPlan, validateDraft } from "@/features/fair-use/limits"; // PIP4
import { pipelinesQuery } from "@/features/pipelines/api"; // PIP4
import type { Plan, UpdatePlanRequest } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { plansStrings as t } from "@/lib/strings/accounts";
import { en, fill } from "@/lib/strings/en";
import { planChoiceStrings } from "@/lib/strings/plan-choice"; // P6 plan choice
import { shopStrings } from "@/lib/strings/shop"; // P5 plans
import { whatsappStrings as wa } from "@/lib/strings/whatsapp"; // TV6
import { useCreatePlan, useUpdatePlan } from "./api";
import { LIMIT_KEYS, type LimitKey, limitText, parseLimit, SIZE_KEYS, type SizeKey } from "./limits";

const limitField = z.string().refine((v) => parseLimit(v) !== undefined, t.dialog.limitInvalid);

// BIZ3
const FEATURE_KEYS = [
	"advanced",
	"models",
	"retrieval",
	"keys_agents",
	"webhooks",
	"quality",
	"api_docs",
	"usage_details",
	"widget_code_only",
] as const;
const featureSchema = z.object(
	Object.fromEntries(FEATURE_KEYS.map((k) => [k, z.boolean()])) as Record<(typeof FEATURE_KEYS)[number], z.ZodBoolean>,
);
// /BIZ3

const planSchema = z.object({
	name: z.string().trim().min(1, t.dialog.nameRequired).max(100, t.dialog.nameLong),
	description: z.string().max(2000, t.dialog.priceLong),
	max_knowledge_bases: limitField,
	monthly_questions: limitField,
	max_documents: limitField,
	max_widget_keys: limitField,
	max_upload_mb: limitField,
	max_document_tokens: limitField,
	count_cached_answers: z.boolean(),
	is_default: z.boolean(),
	// BIZ3
	website_learning_enabled: z.boolean(),
	website_pages_max: limitField,
	dashboard_features: featureSchema,
	// /BIZ3
	// TV6 channel
	whatsapp: z.boolean(),
	whatsapp_fees: z.enum(["pass_through", "included"]),
	// /TV6 channel
	byok_allowed: z.boolean(), // P5 plans
	self_service: z.boolean(), // P6 plan choice
});
type PlanForm = z.infer<typeof planSchema>;

/**
 * Create or edit a plan (mockup `#planDlg`): name, price note (`description`), the four limits
 * (empty = unlimited, whole numbers ≥ 1) and "Default plan". The default plan's checkbox is
 * locked on: the default changes by making another plan the default (A6 `plan_is_default`).
 */
export function PlanDialog({
	open,
	onOpenChange,
	plan,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	/** null = a new plan. */
	plan: Plan | null;
}) {
	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[560px]">
				{open ? <PlanFormBody plan={plan} onDone={() => onOpenChange(false)} /> : null}
			</DialogContent>
		</Dialog>
	);
}

function PlanFormBody({ plan, onDone }: { plan: Plan | null; onDone: () => void }) {
	const ids = { name: useId(), description: useId(), isDefault: useId(), cached: useId() };
	const limitIds = {
		max_knowledge_bases: useId(),
		monthly_questions: useId(),
		max_documents: useId(),
		max_widget_keys: useId(),
		max_upload_mb: useId(),
		max_document_tokens: useId(),
		website_pages_max: useId(), // BIZ3
	};
	const create = useCreatePlan();
	const update = useUpdatePlan();
	const form = useAppForm<PlanForm>({
		resolver: zodResolver(planSchema),
		defaultValues: {
			name: plan?.name ?? "",
			description: plan?.description ?? "",
			max_knowledge_bases: limitText(plan?.max_knowledge_bases),
			monthly_questions: limitText(plan?.monthly_questions),
			max_documents: limitText(plan?.max_documents),
			max_widget_keys: limitText(plan?.max_widget_keys),
			max_upload_mb: limitText(plan?.max_upload_mb),
			max_document_tokens: limitText(plan?.max_document_tokens),
			count_cached_answers: plan?.count_cached_answers ?? true,
			is_default: plan?.is_default ?? false,
			// BIZ3
			website_learning_enabled: plan?.website_learning_enabled ?? true,
			website_pages_max: limitText(plan?.website_pages_max),
			dashboard_features: {
				advanced: plan?.dashboard_features?.advanced ?? true,
				models: plan?.dashboard_features?.models ?? true,
				retrieval: plan?.dashboard_features?.retrieval ?? true,
				keys_agents: plan?.dashboard_features?.keys_agents ?? true,
				webhooks: plan?.dashboard_features?.webhooks ?? true,
				quality: plan?.dashboard_features?.quality ?? true,
				api_docs: plan?.dashboard_features?.api_docs ?? true,
				usage_details: plan?.dashboard_features?.usage_details ?? true,
				widget_code_only: plan?.dashboard_features?.widget_code_only ?? false,
			},
			// /BIZ3
			// TV6 channel
			whatsapp: plan?.channels?.whatsapp ?? false,
			whatsapp_fees: plan?.channels?.whatsapp_fees ?? "pass_through",
			// /TV6 channel
			byok_allowed: plan?.byok_allowed ?? true, // P5 plans
			self_service: plan?.self_service ?? false, // P6 plan choice
		},
	});
	// PIP4
	const profiles = useQuery(pipelinesQuery);
	const [fair, setFair] = useState(() => draftFromPlan(plan));
	const [fairErrors, setFairErrors] = useState<DraftErrors>({});
	// /PIP4
	const errs = form.formState.errors;
	const lockedDefault = !!plan?.is_default;
	useEffect(() => {
		form.setFocus("name");
	}, [form]);

	const submit = useAppSubmit(
		form,
		async (v) => {
			// PIP4
			const fairFound = validateDraft(fair, plan);
			setFairErrors(fairFound);
			if (Object.keys(fairFound).length > 0) return;
			const fairBody = plan?.is_internal ? {} : bodyFromDraft(fair, plan);
			// /PIP4
			const limits = Object.fromEntries(
				[...LIMIT_KEYS, ...SIZE_KEYS].map((k) => [k, parseLimit(v[k]) ?? null]),
			) as Record<LimitKey | SizeKey, number | null>;
			const base = {
				name: v.name.trim(),
				description: v.description.trim() || null,
				count_cached_answers: v.count_cached_answers,
				...limits,
				// BIZ3
				website_learning_enabled: v.website_learning_enabled,
				website_pages_max: parseLimit(v.website_pages_max) ?? null,
				dashboard_features: v.dashboard_features,
				channels: { whatsapp: v.whatsapp, whatsapp_fees: v.whatsapp_fees }, // TV6 channel
				byok_allowed: v.byok_allowed, // P5 plans
				self_service: v.self_service, // P6 plan choice
				...fairBody, // PIP4
				// /BIZ3
			};
			if (plan) {
				const body: UpdatePlanRequest = { ...base };
				if (v.is_default && !plan.is_default) body.is_default = true;
				await update.mutateAsync({ id: plan.id, body });
				toast.success(t.dialog.savedToast, { description: fill(t.dialog.savedToastBody, { name: base.name }) });
			} else {
				await create.mutateAsync({ ...base, is_default: v.is_default });
				toast.success(t.dialog.createdToast, { description: fill(t.dialog.createdToastBody, { name: base.name }) });
			}
			onDone();
		},
		{
			fieldName: (path) => path.split(".")[0] as keyof PlanForm,
			onError: (err) => {
				if (err.code !== "plan_name_taken") return false;
				form.setError("name", { type: "server", message: messageFor(err).title });
				form.setFocus("name");
				return true;
			},
		},
	);

	return (
		<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-4">
			<DialogHeader>
				<DialogTitle>{plan ? fill(t.dialog.editTitle, { name: plan.name }) : t.dialog.createTitle}</DialogTitle>
				<DialogDescription>{t.dialog.lead}</DialogDescription>
			</DialogHeader>
			{submit.error ? <FormAlert>{messageFor(submit.error).title}</FormAlert> : null}
			<div className="grid gap-4 min-[600px]:grid-cols-2">
				<Field id={ids.name} label={t.dialog.name} error={errs.name?.message}>
					<Input
						id={ids.name}
						autoComplete="off"
						placeholder={t.dialog.namePlaceholder}
						aria-invalid={!!errs.name}
						aria-describedby={describedBy(ids.name, { error: !!errs.name })}
						{...form.register("name")}
					/>
				</Field>
				<Field id={ids.description} label={t.dialog.price} hint={t.dialog.priceHint} error={errs.description?.message}>
					<Input
						id={ids.description}
						autoComplete="off"
						aria-invalid={!!errs.description}
						aria-describedby={describedBy(ids.description, { hint: true, error: !!errs.description })}
						{...form.register("description")}
					/>
				</Field>
			</div>
			<div className="grid gap-4 min-[600px]:grid-cols-2">
				{LIMIT_KEYS.map((k) => (
					<Field key={k} id={limitIds[k]} label={t.limits[k]} hint={t.dialog.limitHint} error={errs[k]?.message}>
						<Input
							id={limitIds[k]}
							inputMode="numeric"
							autoComplete="off"
							className="font-mono"
							placeholder="∞"
							aria-invalid={!!errs[k]}
							aria-describedby={describedBy(limitIds[k], { hint: true, error: !!errs[k] })}
							{...form.register(k)}
						/>
					</Field>
				))}
			</div>
			<div className="grid gap-4 min-[600px]:grid-cols-2">
				{SIZE_KEYS.map((k) => (
					<Field
						key={k}
						id={limitIds[k]}
						label={t.limits[k]}
						hint={k === "max_upload_mb" ? t.dialog.uploadMbHint : t.dialog.documentTokensHint}
						error={errs[k]?.message}
					>
						<Input
							id={limitIds[k]}
							inputMode="numeric"
							autoComplete="off"
							className="font-mono"
							placeholder="Engine"
							aria-invalid={!!errs[k]}
							aria-describedby={describedBy(limitIds[k], { hint: true, error: !!errs[k] })}
							{...form.register(k)}
						/>
					</Field>
				))}
			</div>
			{/* BIZ3 */}
			<BusinessSection form={form} pagesId={limitIds.website_pages_max} pagesError={errs.website_pages_max?.message} />
			<WhatsAppSection form={form} />
			<OwnKeyRow form={form} /> {/* P5 plans */}
			<SelfServiceRow form={form} /> {/* P6 plan choice */}
			{/* /BIZ3 */}
			{/* PIP4 */}
			<AnswerSpeedField draft={fair} onChange={setFair} profiles={profiles.data?.items} readOnly={plan?.is_internal} />
			<FairUseFields
				draft={fair}
				onChange={setFair}
				errors={fairErrors}
				profiles={profiles.data?.items}
				readOnly={plan?.is_internal}
			/>
			{/* /PIP4 */}
			<div className="flex items-start gap-2.5">
				<Controller
					control={form.control}
					name="count_cached_answers"
					render={({ field }) => (
						<Checkbox
							id={ids.cached}
							className="mt-0.5"
							checked={field.value}
							aria-describedby={`${ids.cached}-hint`}
							onCheckedChange={(c) => field.onChange(c === true)}
						/>
					)}
				/>
				<div className="grid gap-0.5">
					<label htmlFor={ids.cached} className="text-[13.5px] font-semibold">
						{t.dialog.countCached}
					</label>
					<small id={`${ids.cached}-hint`} className="text-[12.5px] text-muted-foreground">
						{t.dialog.countCachedHint}
					</small>
				</div>
			</div>
			<div className="flex items-start gap-2.5">
				<Controller
					control={form.control}
					name="is_default"
					render={({ field }) => (
						<Checkbox
							id={ids.isDefault}
							className="mt-0.5"
							checked={field.value}
							disabled={lockedDefault}
							aria-describedby={`${ids.isDefault}-hint`}
							onCheckedChange={(c) => field.onChange(c === true)}
						/>
					)}
				/>
				<div className="grid gap-0.5">
					<label htmlFor={ids.isDefault} className="text-[13.5px] font-semibold">
						{t.dialog.isDefault}
					</label>
					<small id={`${ids.isDefault}-hint`} className="text-[12.5px] text-muted-foreground">
						{lockedDefault ? t.dialog.isDefaultLocked : t.dialog.isDefaultHint}
					</small>
				</div>
			</div>
			<DialogFooter>
				<Button type="button" variant="ghost" onClick={onDone}>
					{en.common.cancel}
				</Button>
				<SubmitButton
					variant="default"
					pending={submit.isSubmitting}
					locked={submit.locked}
					pendingLabel={t.dialog.saving}
				>
					{plan ? t.dialog.save : t.dialog.create}
				</SubmitButton>
			</DialogFooter>
		</form>
	);
}

// BIZ3
/** "What businesses on this plan see" and "Website learning": the deployer's per-plan choices. */
function BusinessSection({
	form,
	pagesId,
	pagesError,
}: {
	form: ReturnType<typeof useAppForm<PlanForm>>;
	pagesId: string;
	pagesError: string | undefined;
}) {
	const b = t.business;
	const learningId = useId();
	return (
		<>
			<fieldset className="grid gap-3 rounded-md border border-border p-3.5">
				<legend className="px-1.5 text-[13.5px] font-bold">{b.title}</legend>
				<p className="m-0 text-[12.5px] text-muted-foreground">{b.lead}</p>
				{FEATURE_KEYS.map((k) => (
					<FlagRow key={k} form={form} name={k} label={b.flags[k].label} hint={b.flags[k].hint} />
				))}
			</fieldset>
			<fieldset className="grid gap-3 rounded-md border border-border p-3.5">
				<legend className="px-1.5 text-[13.5px] font-bold">{b.website.title}</legend>
				<div className="flex items-start gap-2.5">
					<Controller
						control={form.control}
						name="website_learning_enabled"
						render={({ field }) => (
							<Checkbox
								id={learningId}
								className="mt-0.5"
								checked={field.value}
								aria-describedby={`${learningId}-hint`}
								onCheckedChange={(c) => field.onChange(c === true)}
							/>
						)}
					/>
					<div className="grid gap-0.5">
						<label htmlFor={learningId} className="text-[13.5px] font-semibold">
							{b.website.enabled}
						</label>
						<small id={`${learningId}-hint`} className="text-[12.5px] text-muted-foreground">
							{b.website.enabledHint}
						</small>
					</div>
				</div>
				<Field id={pagesId} label={b.website.pages} hint={b.website.pagesHint} error={pagesError}>
					<Input
						id={pagesId}
						inputMode="numeric"
						autoComplete="off"
						className="font-mono"
						placeholder="∞"
						aria-invalid={!!pagesError}
						aria-describedby={describedBy(pagesId, { hint: true, error: !!pagesError })}
						{...form.register("website_pages_max")}
					/>
				</Field>
			</fieldset>
		</>
	);
}

// P6 plan choice
/** "Businesses can choose this plan themselves": the sign-up link and the owner's Change plan. */
function SelfServiceRow({ form }: { form: ReturnType<typeof useAppForm<PlanForm>> }) {
	const id = useId();
	return (
		<div className="flex items-start gap-2.5" data-testid="plan-self-service">
			<Controller
				control={form.control}
				name="self_service"
				render={({ field }) => (
					<Checkbox
						id={id}
						className="mt-0.5"
						checked={field.value}
						aria-describedby={`${id}-hint`}
						onCheckedChange={(c) => field.onChange(c === true)}
					/>
				)}
			/>
			<div className="grid gap-0.5">
				<label htmlFor={id} className="text-[13.5px] font-semibold">
					{planChoiceStrings.admin.selfService}
				</label>
				<small id={`${id}-hint`} className="text-[12.5px] text-muted-foreground">
					{planChoiceStrings.admin.selfServiceHint}
				</small>
			</div>
		</div>
	);
}
// /P6 plan choice

// P5 plans
/** "Businesses on this plan may use their own AI key": off keeps a saved key unused. */
function OwnKeyRow({ form }: { form: ReturnType<typeof useAppForm<PlanForm>> }) {
	const id = useId();
	return (
		<div className="flex items-start gap-2.5" data-testid="plan-own-key">
			<Controller
				control={form.control}
				name="byok_allowed"
				render={({ field }) => (
					<Checkbox
						id={id}
						className="mt-0.5"
						checked={field.value}
						aria-describedby={`${id}-hint`}
						onCheckedChange={(c) => field.onChange(c === true)}
					/>
				)}
			/>
			<div className="grid gap-0.5">
				<label htmlFor={id} className="text-[13.5px] font-semibold">
					{shopStrings.admin.planToggle}
				</label>
				<small id={`${id}-hint`} className="text-[12.5px] text-muted-foreground">
					{shopStrings.admin.planToggleHint}
				</small>
			</div>
		</div>
	);
}
// /P5 plans

// TV6 channel
function WhatsAppSection({ form }: { form: ReturnType<typeof useAppForm<PlanForm>> }) {
	const onId = useId();
	const feesId = useId();
	const on = form.watch("whatsapp");
	return (
		<fieldset className="grid gap-3 rounded-md border border-border p-3.5" data-testid="plan-whatsapp">
			<legend className="px-1.5 text-[13.5px] font-bold">{wa.title}</legend>
			<div className="flex items-start gap-2.5">
				<Controller
					control={form.control}
					name="whatsapp"
					render={({ field }) => (
						<Checkbox
							id={onId}
							className="mt-0.5"
							checked={field.value}
							aria-describedby={`${onId}-hint`}
							onCheckedChange={(c) => field.onChange(c === true)}
						/>
					)}
				/>
				<div className="grid gap-0.5">
					<label htmlFor={onId} className="text-[13.5px] font-semibold">
						{wa.plan.included}
					</label>
					<small id={`${onId}-hint`} className="text-[12.5px] text-muted-foreground">
						{wa.plan.includedHelp}
					</small>
				</div>
			</div>
			<Field id={feesId} label={wa.plan.fees} hint={wa.plan.feesHelp}>
				<NativeSelect id={feesId} disabled={!on} {...form.register("whatsapp_fees")}>
					<option value="pass_through">{wa.plan.passThrough}</option>
					<option value="included">{wa.plan.feesIncluded}</option>
				</NativeSelect>
			</Field>
		</fieldset>
	);
}
// /TV6 channel

function FlagRow({
	form,
	name,
	label,
	hint,
}: {
	form: ReturnType<typeof useAppForm<PlanForm>>;
	name: (typeof FEATURE_KEYS)[number];
	label: string;
	hint: string;
}) {
	const id = useId();
	return (
		<div className="flex items-start gap-2.5">
			<Controller
				control={form.control}
				name={`dashboard_features.${name}`}
				render={({ field }) => (
					<Checkbox
						id={id}
						className="mt-0.5"
						checked={field.value}
						aria-describedby={`${id}-hint`}
						onCheckedChange={(c) => field.onChange(c === true)}
					/>
				)}
			/>
			<div className="grid gap-0.5">
				<label htmlFor={id} className="text-[13.5px] font-semibold">
					{label}
				</label>
				<small id={`${id}-hint`} className="text-[12.5px] text-muted-foreground">
					{hint}
				</small>
			</div>
		</div>
	);
}
// /BIZ3
