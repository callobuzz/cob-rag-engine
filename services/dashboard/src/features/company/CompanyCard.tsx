import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Input } from "@/components/ui/input";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { type Company, companyQuery, useSaveCompany } from "@/features/payments/api";
import { GST_STATES } from "@/features/payments/states";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { paymentStrings } from "@/lib/strings/payments";

const t = paymentStrings.admin.tax;
const orNull = (v: string): string | null => (v.trim() === "" ? null : v.trim());

/**
 * The deployer's own business (P1): who the legal pages, tax invoices and payments speak for. Shown
 * on Billing → Tax and (later) next to the legal pages; one model, one place to type it.
 */
export function CompanyCard() {
	const q = useQuery(companyQuery);
	if (q.isPending) return <CardSkeleton lines={5} />;
	if (q.isError) return <ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />;
	return <CompanyForm company={q.data} />;
}

function CompanyForm({ company }: { company: Company }) {
	const id = useId();
	const save = useSaveCompany();
	const [name, setName] = useState(company.legal_name ?? "");
	const [trade, setTrade] = useState(company.trade_name ?? "");
	const [line1, setLine1] = useState(company.address_line1 ?? "");
	const [line2, setLine2] = useState(company.address_line2 ?? "");
	const [city, setCity] = useState(company.city ?? "");
	const [state, setState] = useState(company.state_code ?? "");
	const [pin, setPin] = useState(company.pincode ?? "");
	const [email, setEmail] = useState(company.email ?? "");
	const [phone, setPhone] = useState(company.phone ?? "");
	const [error, setError] = useState<string | null>(null);

	useEffect(() => {
		setName(company.legal_name ?? "");
		setTrade(company.trade_name ?? "");
		setLine1(company.address_line1 ?? "");
		setLine2(company.address_line2 ?? "");
		setCity(company.city ?? "");
		setState(company.state_code ?? "");
		setPin(company.pincode ?? "");
		setEmail(company.email ?? "");
		setPhone(company.phone ?? "");
	}, [company]);

	const submit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		try {
			await save.mutateAsync({
				legal_name: orNull(name),
				trade_name: orNull(trade),
				address_line1: orNull(line1),
				address_line2: orNull(line2),
				city: orNull(city),
				state_code: orNull(state),
				pincode: orNull(pin),
				country: company.country || "IN",
				email: orNull(email),
				phone: orNull(phone),
			});
			toast.success(t.companySaved);
		} catch (err) {
			setError(messageFor(toApiError(err)).title);
		}
	};

	return (
		<form onSubmit={(e) => void submit(e)} noValidate data-testid="company-card">
			<Panel title={t.companyTitle} sub={t.companyLead}>
				<div className="grid gap-4 p-4">
					{error ? <FormAlert>{error}</FormAlert> : null}
					<div className="grid gap-4 min-[600px]:grid-cols-2">
						<Field id={`${id}-name`} label={t.legalName}>
							<Input id={`${id}-name`} maxLength={200} value={name} onChange={(e) => setName(e.target.value)} />
						</Field>
						<Field id={`${id}-trade`} label={t.tradeName}>
							<Input id={`${id}-trade`} maxLength={200} value={trade} onChange={(e) => setTrade(e.target.value)} />
						</Field>
					</div>
					<Field id={`${id}-l1`} label={t.address1}>
						<Input id={`${id}-l1`} maxLength={200} value={line1} onChange={(e) => setLine1(e.target.value)} />
					</Field>
					<Field id={`${id}-l2`} label={t.address2}>
						<Input id={`${id}-l2`} maxLength={200} value={line2} onChange={(e) => setLine2(e.target.value)} />
					</Field>
					<div className="grid gap-4 min-[600px]:grid-cols-3">
						<Field id={`${id}-city`} label={t.city}>
							<Input id={`${id}-city`} maxLength={100} value={city} onChange={(e) => setCity(e.target.value)} />
						</Field>
						<Field id={`${id}-state`} label={t.state}>
							<NativeSelect id={`${id}-state`} value={state} onChange={(e) => setState(e.target.value)}>
								<option value="" />
								{GST_STATES.map((s) => (
									<option key={s.code} value={s.code}>
										{s.name}
									</option>
								))}
							</NativeSelect>
						</Field>
						<Field id={`${id}-pin`} label={t.pincode}>
							<Input
								id={`${id}-pin`}
								inputMode="numeric"
								maxLength={6}
								value={pin}
								onChange={(e) => setPin(e.target.value)}
							/>
						</Field>
					</div>
					<div className="grid gap-4 min-[600px]:grid-cols-2">
						<Field id={`${id}-email`} label={t.email}>
							<Input id={`${id}-email`} type="email" value={email} onChange={(e) => setEmail(e.target.value)} />
						</Field>
						<Field id={`${id}-phone`} label={t.phone}>
							<Input id={`${id}-phone`} maxLength={20} value={phone} onChange={(e) => setPhone(e.target.value)} />
						</Field>
					</div>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={save.isPending}>
						{t.saveCompany}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
