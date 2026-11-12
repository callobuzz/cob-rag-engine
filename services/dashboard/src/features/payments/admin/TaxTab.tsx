import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Input } from "@/components/ui/input";
import { CompanyCard } from "@/features/company/CompanyCard";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { fill } from "@/lib/strings/en";
import { paymentStrings } from "@/lib/strings/payments";
import { type SellerTaxProfileOut, taxProfileQuery, useSaveTaxProfile } from "../api";

const t = paymentStrings.admin.tax;
const orNull = (v: string): string | null => (v.trim() === "" ? null : v.trim());

/** Billing → Tax (P1): the company card, and how the deployer's tax invoices are made. */
export function TaxTab() {
	return (
		<div className="grid gap-6" data-testid="billing-tax">
			<CompanyCard />
			<TaxProfileCard />
		</div>
	);
}

function TaxProfileCard() {
	const q = useQuery(taxProfileQuery);
	if (q.isPending) return <CardSkeleton lines={5} />;
	if (q.isError) return <ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />;
	return <ProfileForm profile={q.data} />;
}

function ProfileForm({ profile }: { profile: SellerTaxProfileOut }) {
	const id = useId();
	const save = useSaveTaxProfile();
	const [gstin, setGstin] = useState(profile.gstin ?? "");
	const [sac, setSac] = useState(profile.sac_default);
	const [prefix, setPrefix] = useState(profile.invoice_prefix);
	const [creditPrefix, setCreditPrefix] = useState(profile.credit_note_prefix);
	const [lut, setLut] = useState(profile.lut_number ?? "");
	const [lutUntil, setLutUntil] = useState(profile.lut_valid_until ?? "");
	const [inclusive, setInclusive] = useState(profile.prices_include_gst);
	const [footer, setFooter] = useState(profile.footer_text ?? "");
	const [signature, setSignature] = useState(profile.signature_line);
	const [error, setError] = useState<string | null>(null);
	const [gstinError, setGstinError] = useState<string | undefined>();

	useEffect(() => {
		setGstin(profile.gstin ?? "");
		setSac(profile.sac_default);
		setPrefix(profile.invoice_prefix);
		setCreditPrefix(profile.credit_note_prefix);
		setLut(profile.lut_number ?? "");
		setLutUntil(profile.lut_valid_until ?? "");
		setInclusive(profile.prices_include_gst);
		setFooter(profile.footer_text ?? "");
		setSignature(profile.signature_line);
	}, [profile]);

	const submit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		setGstinError(undefined);
		try {
			await save.mutateAsync({
				gstin: orNull(gstin)?.toUpperCase() ?? null,
				sac_default: sac.trim(),
				invoice_prefix: prefix.trim().toUpperCase(),
				credit_note_prefix: creditPrefix.trim().toUpperCase(),
				lut_number: orNull(lut),
				lut_valid_until: orNull(lutUntil),
				prices_include_gst: inclusive,
				footer_text: orNull(footer),
				signature_line: signature.trim(),
			});
			toast.success(t.profileSaved);
		} catch (err) {
			const apiErr = toApiError(err);
			if (isApiError(apiErr) && apiErr.code === "gstin_state_mismatch") setGstinError(t.stateMismatch);
			else if (isApiError(apiErr) && apiErr.code === "invalid_gstin") setGstinError(t.invalidGstin);
			else if (isApiError(apiErr) && apiErr.status === 422 && apiErr.fieldErrors.some((f) => f.path === "gstin"))
				setGstinError(t.invalidGstin);
			else setError(messageFor(apiErr).title);
		}
	};

	return (
		<form onSubmit={(e) => void submit(e)} noValidate data-testid="tax-profile">
			<Panel title={t.profileTitle} sub={t.profileLead}>
				<div className="grid gap-4 p-4">
					{error ? <FormAlert>{error}</FormAlert> : null}
					<div className="grid gap-4 min-[600px]:grid-cols-2">
						<Field id={`${id}-gstin`} label={t.gstin} hint={t.gstinHint} error={gstinError}>
							<Input
								id={`${id}-gstin`}
								autoComplete="off"
								className="font-mono uppercase"
								maxLength={40}
								value={gstin}
								aria-invalid={gstinError ? true : undefined}
								onChange={(e) => setGstin(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-sac`} label={t.sac} hint={t.sacHint}>
							<Input
								id={`${id}-sac`}
								inputMode="numeric"
								maxLength={6}
								value={sac}
								onChange={(e) => setSac(e.target.value)}
							/>
						</Field>
					</div>
					<div className="grid gap-4 min-[600px]:grid-cols-2">
						<Field id={`${id}-prefix`} label={t.prefix} hint={fill(t.prefixHint, { example: profile.number_example })}>
							<Input
								id={`${id}-prefix`}
								maxLength={4}
								className="font-mono uppercase"
								value={prefix}
								onChange={(e) => setPrefix(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-cn`} label={t.creditPrefix}>
							<Input
								id={`${id}-cn`}
								maxLength={4}
								className="font-mono uppercase"
								value={creditPrefix}
								onChange={(e) => setCreditPrefix(e.target.value)}
							/>
						</Field>
					</div>
					<p data-testid="number-example" className="m-0 text-[12.5px] text-muted-foreground">
						{fill(t.numberExample, { example: profile.number_example })}
					</p>
					<div className="grid gap-4 min-[600px]:grid-cols-2">
						<Field id={`${id}-lut`} label={t.lut}>
							<Input id={`${id}-lut`} maxLength={40} value={lut} onChange={(e) => setLut(e.target.value)} />
						</Field>
						<Field id={`${id}-lutuntil`} label={t.lutUntil}>
							<Input id={`${id}-lutuntil`} type="date" value={lutUntil} onChange={(e) => setLutUntil(e.target.value)} />
						</Field>
					</div>
					<div className="grid gap-1">
						<label className="flex items-center gap-2 text-[13.5px] font-semibold">
							<input type="checkbox" checked={inclusive} onChange={(e) => setInclusive(e.target.checked)} />
							{t.inclusive}
						</label>
						<small className="text-[12.5px] text-muted-foreground">{t.inclusiveHint}</small>
					</div>
					<Field id={`${id}-footer`} label={t.footer}>
						<Input id={`${id}-footer`} maxLength={500} value={footer} onChange={(e) => setFooter(e.target.value)} />
					</Field>
					<Field id={`${id}-sig`} label={t.signature}>
						<Input id={`${id}-sig`} maxLength={200} value={signature} onChange={(e) => setSignature(e.target.value)} />
					</Field>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={save.isPending}>
						{t.saveProfile}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
