import { ExternalLink } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { type Resolver, type UseFormRegisterReturn, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Form2, FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { safeHttpUrl, useBranding } from "@/lib/branding/BrandingProvider";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { brandingStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { useSaveSettings } from "./api";
import {
	type BrandField,
	type BrandValues,
	brandFieldOf,
	brandPatch,
	HEX_RE,
	LOW_CONTRAST,
	validateBrand,
	whiteContrast,
} from "./brand-form";

const t = brandingStrings.brand;

/** `validateBrand` as a react-hook-form resolver: the same rules, run on blur then on every change. */
const brandResolver =
	(allowSpecialUse: boolean): Resolver<BrandValues> =>
	async (values) => {
		const found = validateBrand(values, allowSpecialUse);
		const entries = Object.entries(found).filter(([, message]) => !!message);
		if (entries.length === 0) return { values, errors: {} };
		return {
			values: {},
			errors: Object.fromEntries(entries.map(([k, message]) => [k, { type: "validate", message }])),
		};
	};

/**
 * Mockup "Brand" card: product name, logo URL (with preview), primary colour (picker + hex, white
 * text contrast), support e-mail, contact link and contact text. Save sends only the changed
 * fields (`null` for a cleared optional one); a colour under 3 : 1 asks first. "cob-rag-engine by Callobuzz" is not
 * a field. `onChange` mirrors every edit to the live preview.
 */
export function BrandForm({ initial, onChange }: { initial: BrandValues; onChange: (v: BrandValues) => void }) {
	const ids = {
		name: useId(),
		logo: useId(),
		color: useId(),
		hex: useId(),
		email: useId(),
		url: useId(),
		text: useId(),
	};
	const save = useSaveSettings();
	const { specialUseEmailsAllowed } = useBranding();
	const form = useAppForm<BrandValues>({ resolver: brandResolver(specialUseEmailsAllowed), defaultValues: initial });
	const errs = form.formState.errors;
	const [confirmColor, setConfirmColor] = useState(false);
	const [savedOnce, setSavedOnce] = useState(false);
	const colorOk = useRef(false);
	const values = useWatch({ control: form.control }) as BrandValues;
	const patch = brandPatch(initial, values);
	const dirty = Object.keys(patch).length > 0;
	const ratio = whiteContrast(values.primary_color ?? "");
	const logo = safeHttpUrl((values.logo_url ?? "").trim());
	const { reset, watch } = form;

	// A save (or another tab) brought new server values: start from them.
	useEffect(() => reset(initial), [initial, reset]);
	// The page's live preview follows every edit.
	useEffect(() => {
		const sub = watch((v) => onChange(v as BrandValues));
		return () => sub.unsubscribe();
	}, [watch, onChange]);

	const submit = useAppSubmit(
		form,
		async () => {
			if (!dirty) return;
			if (patch.primary_color && (whiteContrast(patch.primary_color) ?? 21) < LOW_CONTRAST && !colorOk.current) {
				setConfirmColor(true);
				return;
			}
			colorOk.current = false;
			const r = await save.mutateAsync({ branding: patch });
			setSavedOnce(true);
			toast.success(t.saved, { description: fill(t.savedBody, { name: r.branding.product_name }) });
		},
		{ fieldName: brandFieldOf },
	);
	const hasErrors = Object.keys(errs).length > 0;

	const input = (k: BrandField, id: string, extra: { mono?: boolean; type?: string; placeholder?: string } = {}) => (
		<Input
			id={id}
			type={extra.type ?? "text"}
			autoComplete="off"
			className={cn(extra.mono && "font-mono")}
			placeholder={extra.placeholder}
			aria-invalid={!!errs[k] || undefined}
			aria-describedby={describedBy(id, { error: !!errs[k] })}
			{...form.register(k)}
		/>
	);
	const reg = (k: BrandField): UseFormRegisterReturn => form.register(k);

	return (
		<Panel title={t.title} sub={t.sub}>
			<form onSubmit={(e) => void submit.onSubmit(e)} noValidate aria-label={t.title}>
				<div className="grid gap-4 p-4">
					{submit.error ? (
						<FormAlert>{messageFor(submit.error).title}</FormAlert>
					) : form.formState.submitCount > 0 && hasErrors ? (
						<FormAlert>{t.fixErrors}</FormAlert>
					) : null}
					<Field id={ids.name} label={t.name} error={errs.product_name?.message}>
						{input("product_name", ids.name)}
					</Field>
					<Field id={ids.logo} label={t.logo} hint={t.logoHint} error={errs.logo_url?.message}>
						<div className="flex items-center gap-3">
							<span
								className="grid size-11 shrink-0 place-items-center overflow-hidden rounded-[11px] border border-dashed border-input bg-muted"
								data-testid="logo-preview"
							>
								{logo ? (
									<img src={logo} alt={t.logoPreview} className="size-full object-contain" />
								) : (
									<span aria-hidden className="font-display text-lg font-extrabold text-muted-foreground">
										{((values.product_name ?? "").trim() || "?").charAt(0).toUpperCase()}
									</span>
								)}
							</span>
							<Input
								id={ids.logo}
								type="url"
								inputMode="url"
								autoComplete="off"
								className="font-mono"
								placeholder="https://"
								aria-invalid={!!errs.logo_url || undefined}
								aria-describedby={describedBy(ids.logo, { hint: true, error: !!errs.logo_url })}
								{...reg("logo_url")}
							/>
						</div>
					</Field>
					<Form2>
						<Field id={ids.hex} label={t.color} error={errs.primary_color?.message}>
							<div className="flex items-center gap-2">
								<input
									id={ids.color}
									type="color"
									aria-label={t.colorPick}
									className="h-[38px] w-11 flex-none rounded-sm border border-input bg-muted p-0.5"
									value={
										HEX_RE.test((values.primary_color ?? "").trim())
											? (values.primary_color ?? "").trim().toLowerCase()
											: "#000000"
									}
									onChange={(e) =>
										form.setValue("primary_color", e.target.value, { shouldValidate: true, shouldDirty: true })
									}
								/>
								<Input
									id={ids.hex}
									autoComplete="off"
									spellCheck={false}
									className="font-mono"
									maxLength={7}
									aria-invalid={!!errs.primary_color || undefined}
									aria-describedby={[describedBy(ids.hex, { error: !!errs.primary_color }), `${ids.hex}-contrast`]
										.filter(Boolean)
										.join(" ")}
									{...reg("primary_color")}
								/>
							</div>
							<small
								id={`${ids.hex}-contrast`}
								data-testid="contrast"
								className={cn(
									"text-[12.5px] text-muted-foreground",
									ratio != null && ratio < 4.5 && "font-medium text-warning",
								)}
							>
								{ratio == null
									? " "
									: fill(t.contrast, { ratio: ratio.toFixed(1), verdict: ratio >= 4.5 ? t.readable : t.tooLight })}
							</small>
						</Field>
						<Field id={ids.email} label={t.supportEmail} error={errs.support_email?.message}>
							{input("support_email", ids.email, { type: "email", placeholder: t.supportEmailPlaceholder })}
						</Field>
					</Form2>
					<Field id={ids.url} label={t.contactUrl} hint={t.contactUrlHint} error={errs.contact_url?.message}>
						<Input
							id={ids.url}
							type="url"
							inputMode="url"
							autoComplete="off"
							className="font-mono"
							placeholder="https://wa.me/91…"
							aria-invalid={!!errs.contact_url || undefined}
							aria-describedby={describedBy(ids.url, { hint: true, error: !!errs.contact_url })}
							{...reg("contact_url")}
						/>
					</Field>
					<Field id={ids.text} label={t.contactText} hint={t.contactTextHint} error={errs.contact_text?.message}>
						<Textarea
							id={ids.text}
							rows={3}
							maxLength={500}
							aria-invalid={!!errs.contact_text || undefined}
							aria-describedby={describedBy(ids.text, { hint: true, error: !!errs.contact_text })}
							{...reg("contact_text")}
						/>
					</Field>
					<p className="m-0 text-[12.5px] text-muted-foreground" data-testid="powered-note">
						{t.powered}
					</p>
				</div>
				<FormFoot>
					{savedOnce && !dirty ? (
						<a
							href="/login"
							target="_blank"
							rel="noopener noreferrer"
							className="mr-auto inline-flex items-center gap-1.5 self-center text-[13px] font-semibold text-brand-text"
						>
							{t.openLogin}
							<ExternalLink className="size-3.5" aria-hidden />
						</a>
					) : null}
					<Button
						type="button"
						variant="ghost"
						disabled={!dirty || submit.isSubmitting}
						onClick={() => {
							reset(initial);
							onChange(initial);
							submit.clearError();
						}}
					>
						{t.discard}
					</Button>
					<SubmitButton
						variant="default"
						disabled={!dirty}
						pending={submit.isSubmitting}
						locked={submit.locked}
						pendingLabel={t.saving}
					>
						{t.save}
					</SubmitButton>
				</FormFoot>
			</form>
			<ConfirmDialog
				open={confirmColor}
				onOpenChange={setConfirmColor}
				title={t.lowContrastTitle}
				body={fill(t.lowContrastBody, {
					color: (values.primary_color ?? "").trim(),
					ratio: (whiteContrast(values.primary_color ?? "") ?? 1).toFixed(1),
				})}
				confirmLabel={t.lowContrastConfirm}
				destructive={false}
				pending={submit.isSubmitting}
				onConfirm={() => {
					colorOk.current = true;
					setConfirmColor(false);
					void submit.onSubmit();
				}}
			/>
		</Panel>
	);
}
