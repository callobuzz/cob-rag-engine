import { useQuery } from "@tanstack/react-query";
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
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { en } from "@/lib/strings/en";
import { paymentStrings } from "@/lib/strings/payments";
import { type TaxDetailsBody, taxDetailsQuery, useSaveTaxDetails } from "./api";
import { GST_STATES, stateOfGstin } from "./states";

const t = paymentStrings.taxDetails;
const LATIN = /^[\x20-\x7EÀ-ɏ]+$/;

type FieldKey = "legal_name" | "address_line1" | "city" | "state_code" | "pincode" | "gstin" | "email_for_invoices";

/**
 * Who the tax invoices are made out to (P1). The GST number is optional; with one, its state wins.
 * Names and addresses are English letters, as on the GST portal (the invoice PDF cannot shape other
 * scripts). `onSaved` lets the caller carry on (the Add money dialog continues to the pay page).
 */
export function TaxDetailsDialog({
	open,
	onOpenChange,
	onSaved,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onSaved?: () => void;
}) {
	const id = useId();
	const details = useQuery(taxDetailsQuery);
	const save = useSaveTaxDetails();
	const [name, setName] = useState("");
	const [gstin, setGstin] = useState("");
	const [line1, setLine1] = useState("");
	const [line2, setLine2] = useState("");
	const [city, setCity] = useState("");
	const [state, setState] = useState("");
	const [pincode, setPincode] = useState("");
	const [email, setEmail] = useState("");
	const [errors, setErrors] = useState<Partial<Record<FieldKey, string>>>({});
	const [failure, setFailure] = useState<string | null>(null);

	useEffect(() => {
		if (!open) return;
		const d = details.data;
		setName(d?.legal_name ?? "");
		setGstin(d?.gstin ?? "");
		setLine1(d?.address_line1 ?? "");
		setLine2(d?.address_line2 ?? "");
		setCity(d?.city ?? "");
		setState(d?.state_code ?? "");
		setPincode(d?.pincode ?? "");
		setEmail(d?.email_for_invoices ?? "");
		setErrors({});
		setFailure(null);
	}, [open, details.data]);

	const fromGstin = stateOfGstin(gstin);
	const shownState = fromGstin ?? state;

	const validate = (): Partial<Record<FieldKey, string>> => {
		const e: Partial<Record<FieldKey, string>> = {};
		const latin = (v: string, key: FieldKey, required: boolean) => {
			if (required && v.trim() === "") e[key] = t.required;
			else if (v.trim() !== "" && !LATIN.test(v.trim())) e[key] = t.latin;
		};
		latin(name, "legal_name", true);
		latin(line1, "address_line1", true);
		latin(city, "city", true);
		if (line2.trim() !== "" && !LATIN.test(line2.trim())) e.address_line1 ??= t.latin;
		if (shownState === "") e.state_code = t.required;
		if (pincode.trim() !== "" && !/^[1-9][0-9]{5}$/.test(pincode.trim())) e.pincode = t.invalid;
		return e;
	};

	const submit = async () => {
		setFailure(null);
		const found = validate();
		setErrors(found);
		if (Object.keys(found).length > 0) return;
		const body: TaxDetailsBody = {
			legal_name: name.trim(),
			gstin: gstin.trim() === "" ? null : gstin.trim().toUpperCase(),
			address_line1: line1.trim(),
			address_line2: line2.trim() === "" ? null : line2.trim(),
			city: city.trim(),
			state_code: shownState || null,
			pincode: pincode.trim() === "" ? null : pincode.trim(),
			country: "IN",
			email_for_invoices: email.trim() === "" ? null : email.trim(),
		};
		try {
			await save.mutateAsync(body);
			toast.success(t.saved);
			onOpenChange(false);
			onSaved?.();
		} catch (err) {
			const e = toApiError(err);
			if (isApiError(e) && e.code === "invalid_gstin") setErrors({ gstin: t.invalidGstin });
			else setFailure(messageFor(e).title);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[560px]">
				<form
					noValidate
					className="grid gap-4"
					data-testid="tax-details-form"
					onSubmit={(e) => {
						e.preventDefault();
						void submit();
					}}
				>
					<DialogHeader>
						<DialogTitle>{t.title}</DialogTitle>
						<DialogDescription>{t.lead}</DialogDescription>
					</DialogHeader>
					{failure ? <FormAlert>{failure}</FormAlert> : null}
					<Field id={`${id}-name`} label={t.legalName} hint={t.legalNameHint} error={errors.legal_name}>
						<Input
							id={`${id}-name`}
							autoComplete="organization"
							maxLength={200}
							value={name}
							aria-invalid={errors.legal_name ? true : undefined}
							onChange={(e) => setName(e.target.value)}
						/>
					</Field>
					<Field id={`${id}-gstin`} label={t.gstin} hint={t.gstinHint} error={errors.gstin}>
						<Input
							id={`${id}-gstin`}
							autoComplete="off"
							maxLength={40}
							className="font-mono uppercase"
							value={gstin}
							aria-invalid={errors.gstin ? true : undefined}
							onChange={(e) => setGstin(e.target.value)}
						/>
					</Field>
					<Field id={`${id}-line1`} label={t.address1} error={errors.address_line1}>
						<Input
							id={`${id}-line1`}
							autoComplete="address-line1"
							maxLength={200}
							value={line1}
							aria-invalid={errors.address_line1 ? true : undefined}
							onChange={(e) => setLine1(e.target.value)}
						/>
					</Field>
					<Field id={`${id}-line2`} label={t.address2}>
						<Input
							id={`${id}-line2`}
							autoComplete="address-line2"
							maxLength={200}
							value={line2}
							onChange={(e) => setLine2(e.target.value)}
						/>
					</Field>
					<div className="grid gap-4 min-[520px]:grid-cols-2">
						<Field id={`${id}-city`} label={t.city} error={errors.city}>
							<Input
								id={`${id}-city`}
								autoComplete="address-level2"
								maxLength={100}
								value={city}
								aria-invalid={errors.city ? true : undefined}
								onChange={(e) => setCity(e.target.value)}
							/>
						</Field>
						<Field id={`${id}-pin`} label={t.pincode} error={errors.pincode}>
							<Input
								id={`${id}-pin`}
								inputMode="numeric"
								autoComplete="postal-code"
								maxLength={6}
								value={pincode}
								aria-invalid={errors.pincode ? true : undefined}
								onChange={(e) => setPincode(e.target.value)}
							/>
						</Field>
					</div>
					<Field
						id={`${id}-state`}
						label={t.state}
						hint={fromGstin ? t.stateFromGstin : undefined}
						error={errors.state_code}
					>
						<NativeSelect
							id={`${id}-state`}
							value={shownState}
							disabled={fromGstin !== null}
							aria-invalid={errors.state_code ? true : undefined}
							onChange={(e) => setState(e.target.value)}
						>
							<option value="">{t.statePick}</option>
							{GST_STATES.map((s) => (
								<option key={s.code} value={s.code}>
									{s.name}
								</option>
							))}
						</NativeSelect>
					</Field>
					<Field id={`${id}-email`} label={t.email} hint={t.emailHint} error={errors.email_for_invoices}>
						<Input
							id={`${id}-email`}
							type="email"
							autoComplete="off"
							value={email}
							onChange={(e) => setEmail(e.target.value)}
						/>
					</Field>
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={save.isPending} pendingLabel={t.saving}>
							{t.save}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
