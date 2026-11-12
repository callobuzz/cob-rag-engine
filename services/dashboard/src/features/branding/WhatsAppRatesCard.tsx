import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Input } from "@/components/ui/input";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { infoQuery } from "@/lib/api/queries";
import { fill } from "@/lib/strings/en";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { type Settings, useSaveSettings } from "./api";

type Currency = "INR" | "USD";
type Category = "service" | "utility" | "marketing" | "authentication";
const CATEGORIES: Category[] = ["service", "utility", "marketing", "authentication"];
const CURRENCIES: { code: Currency; minor: string; example: string }[] = [
	{ code: "INR", minor: "paise", example: "35 paise" },
	{ code: "USD", minor: "cents", example: "35 cents" },
];

type Rates = NonNullable<Settings["whatsapp"]>["rates_minor"];

/** Whole minor units, 0 to 1,000,000, or null when the text is not that. */
export function parseRate(text: string): number | null {
	if (text.trim() === "") return 0;
	if (!/^\d{1,7}$/.test(text.trim())) return null;
	const n = Number(text);
	return n <= 1_000_000 ? n : null;
}

/** The text boxes' values as the settings patch the engine takes, or null when one is not valid. */
export function ratesPatch(
	values: Record<Currency, Record<Category, string>>,
	free: string,
): { rates_minor: Record<Currency, Record<Category, number>>; free_service_per_month: number } | null {
	const rates = {} as Record<Currency, Record<Category, number>>;
	for (const { code } of CURRENCIES) {
		const row = {} as Record<Category, number>;
		for (const c of CATEGORIES) {
			const n = parseRate(values[code][c]);
			if (n === null) return null;
			row[c] = n;
		}
		rates[code] = row;
	}
	const freeCount = parseRate(free);
	return freeCount === null ? null : { rates_minor: rates, free_service_per_month: freeCount };
}

/**
 * The deployer's Meta fee rates (TV6): what Meta charges per message, typed in from Meta's own
 * price list for each currency. Nothing is built in: every rate starts at zero, and a rate of zero
 * charges nothing.
 */
export function WhatsAppRatesCard({ whatsapp }: { whatsapp: Settings["whatsapp"] }) {
	const id = useId();
	const save = useSaveSettings();
	// OS3: the engine sends this flag to the operator only, and the operator lives on this page
	const info = useQuery(infoQuery);
	const needsAddress =
		(info.data?.features.whatsapp as { public_address_missing?: boolean } | undefined)?.public_address_missing === true;
	// /OS3
	const stored: Rates = whatsapp?.rates_minor ?? {};
	const [values, setValues] = useState(() => {
		const out = {} as Record<Currency, Record<Category, string>>;
		for (const { code } of CURRENCIES) {
			const row = stored[code];
			out[code] = Object.fromEntries(CATEGORIES.map((c) => [c, String(row?.[c] ?? 0)])) as Record<Category, string>;
		}
		return out;
	});
	const [free, setFree] = useState(String(whatsapp?.free_service_per_month ?? 0));
	const [error, setError] = useState<string | null>(null);

	const submit = async (e: React.FormEvent) => {
		e.preventDefault();
		setError(null);
		const patch = ratesPatch(values, free);
		if (!patch) return setError("Use whole numbers only (paise or cents), from 0 to 1,000,000.");
		try {
			await save.mutateAsync({ whatsapp: patch });
			toast.success(t.rates.saved);
		} catch (err) {
			setError(messageFor(toApiError(err)).title);
		}
	};

	return (
		<form onSubmit={(e) => void submit(e)} noValidate data-testid="whatsapp-rates">
			<Panel title={t.rates.title} sub={t.rates.lead}>
				{needsAddress ? (
					<p
						role="note"
						data-testid="whatsapp-public-address"
						className="m-4 mb-0 rounded-md border bg-muted/40 p-3 text-[13px]"
					>
						{t.publicAddressMissing}
					</p>
				) : null}
				<div className="grid gap-4 p-4">
					{error ? <FormAlert>{error}</FormAlert> : null}
					{CURRENCIES.map(({ code, minor, example }) => (
						<fieldset key={code} className="grid gap-3 rounded-md border border-border p-3">
							<legend className="px-1 text-[13px] font-semibold">{fill(t.rates.currency, { currency: code })}</legend>
							<p className="m-0 text-[12.5px] text-muted-foreground">{fill(t.rates.unit, { minor, example })}</p>
							<div className="grid gap-3 min-[600px]:grid-cols-2">
								{CATEGORIES.map((c) => (
									<Field key={c} id={`${id}-${code}-${c}`} label={t.rates[c]}>
										<Input
											id={`${id}-${code}-${c}`}
											inputMode="numeric"
											value={values[code][c]}
											onChange={(e) => setValues((v) => ({ ...v, [code]: { ...v[code], [c]: e.target.value } }))}
										/>
									</Field>
								))}
							</div>
						</fieldset>
					))}
					<Field id={`${id}-free`} label={t.rates.free} hint={t.rates.freeHint}>
						<Input id={`${id}-free`} inputMode="numeric" value={free} onChange={(e) => setFree(e.target.value)} />
					</Field>
				</div>
				<FormFoot>
					<SubmitButton variant="default" pending={save.isPending}>
						{t.rates.save}
					</SubmitButton>
				</FormFoot>
			</Panel>
		</form>
	);
}
