import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
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
import { Textarea } from "@/components/ui/textarea";
import type { AddOn, AddOnKind, CreateAddOnRequest, UpdateAddOnRequest } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { type Currency, formatMinor, minorToInput, parseMajor } from "@/lib/format/money";
import { addOnStrings as s } from "@/lib/strings/billing-addons";
import { en, fill } from "@/lib/strings/en";
import { shopStrings } from "@/lib/strings/shop"; // P5 plans
import { useCreateAddOn, useUpdateAddOn } from "./api";
import { addOnSummary, creditTotal, KINDS } from "./summary";

type LimitField = NonNullable<AddOn["limit_field"]>;
type Duration = NonNullable<AddOn["duration"]>;
const LIMIT_FIELDS: readonly LimitField[] = [
	"max_knowledge_bases",
	"max_documents",
	"max_widget_keys",
	"max_upload_mb",
	"max_document_tokens",
];

interface Draft {
	kind: AddOnKind;
	name: string;
	description: string;
	currency: Currency;
	price: string;
	credit: string;
	bonus: string;
	expiry: string;
	limitField: LimitField;
	limitDelta: string;
	duration: Duration;
	questions: string;
	// P5 plans
	feature: "byok";
	renews: boolean;
	supportMonths: string;
	sac: string;
	soldToOwners: boolean;
	// /P5 plans
}

const EMPTY: Draft = {
	kind: "question_pack",
	name: "",
	description: "",
	currency: "INR",
	price: "",
	credit: "",
	bonus: "0",
	expiry: "",
	limitField: "max_documents",
	limitDelta: "",
	duration: "period",
	questions: "",
	feature: "byok", // P5 plans
	renews: false,
	supportMonths: "",
	sac: "",
	soldToOwners: false,
};

const draftOf = (a: AddOn): Draft => ({
	kind: a.kind,
	name: a.name,
	description: a.description ?? "",
	currency: a.currency,
	price: minorToInput(a.price_minor),
	credit: a.credit_minor == null ? "" : minorToInput(a.credit_minor),
	bonus: String(a.bonus_pct),
	expiry: a.expiry_days == null ? "" : String(a.expiry_days),
	limitField: (a.limit_field ?? "max_documents") as LimitField,
	limitDelta: a.limit_delta == null ? "" : String(a.limit_delta),
	duration: (a.duration ?? "period") as Duration,
	questions: a.questions == null ? "" : String(a.questions),
	feature: a.feature ?? "byok", // P5 plans
	renews: a.renews,
	supportMonths: a.includes_support_months == null ? "" : String(a.includes_support_months),
	sac: a.sac_code ?? "",
	soldToOwners: a.sold_to_owners,
});

const WHOLE = /^\d+$/;
const whole = (text: string): number | null => (WHOLE.test(text.trim()) ? Number(text.trim()) : null);

type Errors = Partial<
	Record<"name" | "price" | "credit" | "bonus" | "expiry" | "delta" | "questions" | "supportMonths" | "sac", string>
>; // P5 plans: supportMonths, sac

/** The request a draft makes, or the errors that stop it. */
export function toRequest(d: Draft): { body: CreateAddOnRequest | null; errors: Errors } {
	const errors: Errors = {};
	if (d.name.trim() === "") errors.name = s.dialog.errors.name;
	const price = d.price.trim() === "" ? 0 : parseMajor(d.price);
	if (price === null || price < 0) errors.price = s.dialog.errors.price;
	const body: CreateAddOnRequest = {
		kind: d.kind,
		name: d.name.trim(),
		currency: d.currency,
		price_minor: price ?? 0,
		bonus_pct: 0,
		renews: false, // P5 plans
		sold_to_owners: d.soldToOwners,
	};
	if (d.sac.trim() !== "") {
		if (/^99\d{4}$/.test(d.sac.trim())) body.sac_code = d.sac.trim();
		else errors.sac = shopStrings.admin.errors.sac;
	}
	if (d.description.trim()) body.description = d.description.trim();
	if (d.kind === "credit_pack") {
		const credit = parseMajor(d.credit);
		if (credit === null || credit <= 0) errors.credit = s.dialog.errors.credit;
		else body.credit_minor = credit;
		const bonus = d.bonus.trim() === "" ? 0 : whole(d.bonus);
		if (bonus === null || bonus > 100) errors.bonus = s.dialog.errors.bonus;
		else body.bonus_pct = bonus;
		if (d.expiry.trim() !== "") {
			const days = whole(d.expiry);
			if (days === null || days < 1 || days > 3650) errors.expiry = s.dialog.errors.expiry;
			else body.expiry_days = days;
		}
	} else if (d.kind === "extra_limit") {
		const delta = whole(d.limitDelta);
		if (delta === null || delta < 1) errors.delta = s.dialog.errors.delta;
		else body.limit_delta = delta;
		body.limit_field = d.limitField;
		body.duration = d.duration;
	} else if (d.kind === "feature_unlock") {
		// P5 plans: a named feature, for a month or for good; only a monthly one can renew
		body.feature = d.feature;
		body.duration = d.duration;
		body.renews = d.renews && d.duration === "period";
	} else if (d.kind === "service") {
		if (d.supportMonths.trim() !== "") {
			const months = whole(d.supportMonths);
			if (months === null || months < 1 || months > 36) errors.supportMonths = shopStrings.admin.errors.supportMonths;
			else body.includes_support_months = months;
		}
		body.renews = d.renews;
	} else {
		const n = whole(d.questions);
		if (n === null || n < 1) errors.questions = s.dialog.errors.questions;
		else body.questions = n;
	}
	return { body: Object.keys(errors).length === 0 ? body : null, errors };
}

/** What the customer gets, from a draft: the same sentence as the catalogue card. */
export function previewOf(d: Draft): string | null {
	const { body } = toRequest(d);
	if (!body) return null;
	if (body.kind === "credit_pack" && body.credit_minor != null) {
		const pct = body.bonus_pct ?? 0;
		const currency = body.currency as Currency;
		return pct
			? fill(s.dialog.previewCredit, {
					total: formatMinor(creditTotal(body.credit_minor, pct), currency),
					credit: formatMinor(body.credit_minor, currency),
					pct,
				})
			: fill(s.dialog.previewCreditPlain, { total: formatMinor(body.credit_minor, currency) });
	}
	return addOnSummary({
		kind: body.kind,
		currency: body.currency,
		credit_minor: null,
		bonus_pct: 0,
		expiry_days: null,
		limit_field: body.limit_field ?? null,
		limit_delta: body.limit_delta ?? null,
		questions: body.questions ?? null,
		duration: body.duration ?? null,
		feature: body.feature ?? null, // P5 plans
		renews: body.renews,
		includes_support_months: body.includes_support_months ?? null,
	});
}

/**
 * New or edit add-on (BIL5): the fields of its kind only, with a preview line. The kind cannot
 * change once created. Existing grants keep what they were given, so editing is always safe.
 */
export function AddOnDialog({
	open,
	onOpenChange,
	editing,
	defaultCurrency = "INR",
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	editing: AddOn | null;
	defaultCurrency?: Currency;
}) {
	const baseId = useId();
	const create = useCreateAddOn();
	const update = useUpdateAddOn();
	const [draft, setDraft] = useState<Draft>(EMPTY);
	const [submitted, setSubmitted] = useState(false);
	const [failure, setFailure] = useState<string | null>(null);

	useEffect(() => {
		if (!open) return;
		setDraft(editing ? draftOf(editing) : { ...EMPTY, currency: defaultCurrency });
		setSubmitted(false);
		setFailure(null);
	}, [open, editing, defaultCurrency]);

	const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));
	// After the first try the messages follow what is typed, so they clear as the field is fixed.
	const errors: Errors = submitted ? toRequest(draft).errors : {};
	const preview = previewOf(draft);
	const pending = create.isPending || update.isPending;

	const submit = async () => {
		setFailure(null);
		setSubmitted(true);
		const { body } = toRequest(draft);
		if (!body) return;
		try {
			if (editing) {
				const { kind: _kind, ...changes } = body;
				await update.mutateAsync({ id: editing.id, body: nullMissing(changes, body.kind) });
				toast.success(s.updated, { description: fill(s.updatedBody, { name: body.name }) });
			} else {
				await create.mutateAsync(body);
				toast.success(s.created, { description: fill(s.createdBody, { name: body.name }) });
			}
			onOpenChange(false);
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
		}
	};

	const err = (k: keyof Errors) => ({
		"aria-invalid": errors[k] ? (true as const) : undefined,
		"aria-describedby": errors[k] ? `${baseId}-${k}-error` : undefined,
	});

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[520px]">
				<form
					noValidate
					className="grid gap-4"
					onSubmit={(e) => {
						e.preventDefault();
						void submit();
					}}
				>
					<DialogHeader>
						<DialogTitle>{editing ? fill(s.dialog.titleEdit, { name: editing.name }) : s.dialog.titleNew}</DialogTitle>
						<DialogDescription>{s.dialog.lead}</DialogDescription>
					</DialogHeader>
					{failure ? <FormAlert>{failure}</FormAlert> : null}
					<Field id={`${baseId}-kind`} label={s.dialog.kind}>
						<NativeSelect
							id={`${baseId}-kind`}
							value={draft.kind}
							disabled={editing !== null}
							onChange={(e) => set("kind", e.target.value as AddOnKind)}
						>
							{KINDS.map((k) => (
								<option key={k} value={k}>
									{s.kinds[k]}
								</option>
							))}
						</NativeSelect>
					</Field>
					<Field id={`${baseId}-name`} label={s.dialog.name} hint={s.dialog.nameHint} error={errors.name}>
						<Input
							id={`${baseId}-name`}
							maxLength={100}
							value={draft.name}
							{...err("name")}
							onChange={(e) => set("name", e.target.value)}
						/>
					</Field>
					<Field id={`${baseId}-description`} label={s.dialog.description} hint={s.dialog.descriptionHint}>
						<Textarea
							id={`${baseId}-description`}
							rows={2}
							maxLength={1000}
							value={draft.description}
							onChange={(e) => set("description", e.target.value)}
						/>
					</Field>
					<div className="grid gap-4 min-[520px]:grid-cols-2">
						<Field id={`${baseId}-currency`} label={s.dialog.currency}>
							<NativeSelect
								id={`${baseId}-currency`}
								value={draft.currency}
								onChange={(e) => set("currency", e.target.value as Currency)}
							>
								<option value="INR">₹ INR</option>
								<option value="USD">$ USD</option>
							</NativeSelect>
						</Field>
						<Field id={`${baseId}-price`} label={s.dialog.price} hint={s.dialog.priceHint} error={errors.price}>
							<Input
								id={`${baseId}-price`}
								inputMode="decimal"
								autoComplete="off"
								value={draft.price}
								{...err("price")}
								onChange={(e) => set("price", e.target.value)}
							/>
						</Field>
					</div>
					{draft.kind === "credit_pack" ? (
						<>
							<Field id={`${baseId}-credit`} label={s.dialog.credit} error={errors.credit}>
								<Input
									id={`${baseId}-credit`}
									inputMode="decimal"
									autoComplete="off"
									value={draft.credit}
									{...err("credit")}
									onChange={(e) => set("credit", e.target.value)}
								/>
							</Field>
							<div className="grid gap-4 min-[520px]:grid-cols-2">
								<Field id={`${baseId}-bonus`} label={s.dialog.bonus} hint={s.dialog.bonusHint} error={errors.bonus}>
									<Input
										id={`${baseId}-bonus`}
										inputMode="numeric"
										value={draft.bonus}
										{...err("bonus")}
										onChange={(e) => set("bonus", e.target.value)}
									/>
								</Field>
								<Field id={`${baseId}-expiry`} label={s.dialog.expiry} hint={s.dialog.expiryHint} error={errors.expiry}>
									<Input
										id={`${baseId}-expiry`}
										inputMode="numeric"
										value={draft.expiry}
										{...err("expiry")}
										onChange={(e) => set("expiry", e.target.value)}
									/>
								</Field>
							</div>
						</>
					) : null}
					{draft.kind === "extra_limit" ? (
						<>
							<Field id={`${baseId}-field`} label={s.dialog.limitField}>
								<NativeSelect
									id={`${baseId}-field`}
									value={draft.limitField}
									onChange={(e) => set("limitField", e.target.value as LimitField)}
								>
									{LIMIT_FIELDS.map((f) => (
										<option key={f} value={f}>
											{s.limits[f]}
										</option>
									))}
								</NativeSelect>
							</Field>
							<div className="grid gap-4 min-[520px]:grid-cols-2">
								<Field id={`${baseId}-delta`} label={s.dialog.limitDelta} error={errors.delta}>
									<Input
										id={`${baseId}-delta`}
										inputMode="numeric"
										value={draft.limitDelta}
										{...err("delta")}
										onChange={(e) => set("limitDelta", e.target.value)}
									/>
								</Field>
								<Field id={`${baseId}-duration`} label={s.dialog.duration}>
									<NativeSelect
										id={`${baseId}-duration`}
										value={draft.duration}
										onChange={(e) => set("duration", e.target.value as Duration)}
									>
										<option value="period">{s.durations.period}</option>
										<option value="permanent">{s.durations.permanent}</option>
									</NativeSelect>
								</Field>
							</div>
						</>
					) : null}
					{/* P5 plans */}
					{draft.kind === "feature_unlock" ? (
						<>
							<div className="grid gap-4 min-[520px]:grid-cols-2">
								<Field id={`${baseId}-feature`} label={shopStrings.admin.feature}>
									<NativeSelect
										id={`${baseId}-feature`}
										value={draft.feature}
										onChange={(e) => set("feature", e.target.value as "byok")}
									>
										<option value="byok">{shopStrings.admin.features.byok}</option>
									</NativeSelect>
								</Field>
								<Field id={`${baseId}-duration`} label={s.dialog.duration}>
									<NativeSelect
										id={`${baseId}-duration`}
										value={draft.duration}
										onChange={(e) => set("duration", e.target.value as Duration)}
									>
										<option value="period">{s.durations.period}</option>
										<option value="permanent">{s.durations.permanent}</option>
									</NativeSelect>
								</Field>
							</div>
							<CheckField
								id={`${baseId}-renews`}
								checked={draft.renews && draft.duration === "period"}
								disabled={draft.duration !== "period"}
								label={shopStrings.admin.renews}
								hint={shopStrings.admin.renewsHint}
								onChange={(v) => set("renews", v)}
							/>
						</>
					) : null}
					{draft.kind === "service" ? (
						<>
							<Field
								id={`${baseId}-support`}
								label={shopStrings.admin.supportMonths}
								hint={shopStrings.admin.supportMonthsHint}
								error={errors.supportMonths}
							>
								<Input
									id={`${baseId}-support`}
									inputMode="numeric"
									value={draft.supportMonths}
									{...err("supportMonths")}
									onChange={(e) => set("supportMonths", e.target.value)}
								/>
							</Field>
							<CheckField
								id={`${baseId}-renews`}
								checked={draft.renews}
								label={shopStrings.admin.renews}
								hint={shopStrings.admin.renewsHint}
								onChange={(v) => set("renews", v)}
							/>
						</>
					) : null}
					{draft.kind === "feature_unlock" || draft.kind === "service" ? (
						<Field
							id={`${baseId}-sac`}
							label={shopStrings.admin.sac}
							hint={shopStrings.admin.sacHint}
							error={errors.sac}
						>
							<Input
								id={`${baseId}-sac`}
								inputMode="numeric"
								maxLength={6}
								value={draft.sac}
								{...err("sac")}
								onChange={(e) => set("sac", e.target.value)}
							/>
						</Field>
					) : null}
					<CheckField
						id={`${baseId}-owners`}
						checked={draft.soldToOwners}
						label={shopStrings.admin.soldToOwners}
						hint={shopStrings.admin.soldToOwnersHint}
						onChange={(v) => set("soldToOwners", v)}
					/>
					{/* /P5 plans */}
					{draft.kind === "question_pack" ? (
						<Field id={`${baseId}-questions`} label={s.dialog.questions} error={errors.questions}>
							<Input
								id={`${baseId}-questions`}
								inputMode="numeric"
								value={draft.questions}
								{...err("questions")}
								onChange={(e) => set("questions", e.target.value)}
							/>
						</Field>
					) : null}
					<p
						data-testid="add-on-preview"
						className="m-0 rounded-md border border-border bg-secondary px-3 py-2 text-[13.5px]"
					>
						<b className="mr-1">{s.dialog.previewLabel}:</b>
						{preview ?? s.dialog.previewNothing}
					</p>
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={pending} pendingLabel={s.dialog.submitting}>
							{editing ? s.dialog.submitEdit : s.dialog.submitNew}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}

/**
 * A PATCH carries only what the kind uses; the fields of other kinds stay out of the body, and a
 * cleared optional field (an expiry emptied) is sent as null so the server removes it.
 */
function nullMissing(changes: Omit<CreateAddOnRequest, "kind">, kind: AddOnKind): UpdateAddOnRequest {
	const out: UpdateAddOnRequest = { ...changes };
	if (kind === "credit_pack" && out.expiry_days === undefined) out.expiry_days = null;
	if (kind === "service" && out.includes_support_months === undefined) out.includes_support_months = null; // P5 plans
	if (out.sac_code === undefined) out.sac_code = null;
	if (out.description === undefined) out.description = null;
	return out;
}

/** P5 plans: a labelled checkbox with a hint, in the dialog's own style. */
function CheckField({
	id,
	checked,
	disabled = false,
	label,
	hint,
	onChange,
}: {
	id: string;
	checked: boolean;
	disabled?: boolean;
	label: string;
	hint: string;
	onChange: (value: boolean) => void;
}) {
	return (
		<div className="grid gap-0.5">
			<label htmlFor={id} className="flex items-center gap-2 text-[13.5px] font-medium">
				<input
					id={id}
					type="checkbox"
					checked={checked}
					disabled={disabled}
					aria-describedby={`${id}-hint`}
					onChange={(e) => onChange(e.target.checked)}
					className="size-4 accent-[var(--primary)]"
				/>
				{label}
			</label>
			<small id={`${id}-hint`} className="pl-6 text-[12px] text-muted-foreground">
				{hint}
			</small>
		</div>
	);
}
