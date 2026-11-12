import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
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
import { formatMinor, parseMajor } from "@/lib/format/money";
import { en, fill } from "@/lib/strings/en";
import { paymentStrings } from "@/lib/strings/payments";
import { paymentConfigQuery, quoteQuery } from "./api";
import { goToPay } from "./checkout";

const s = paymentStrings.addMoney;
const QUICK = [50_000, 100_000, 200_000, 500_000];

/** The text under the amount: "₹1,000 for your balance + ₹180 GST = ₹1,180 to pay". */
export function previewLine(q: { taxable_minor: number; tax_minor: number; gross_minor: number }): string {
	const money = (n: number) => formatMinor(n, "INR");
	return q.tax_minor > 0
		? fill(s.preview, { base: money(q.taxable_minor), tax: money(q.tax_minor), total: money(q.gross_minor) })
		: fill(s.previewNoTax, { total: money(q.gross_minor) });
}

/**
 * "Add money" (P1): choose an amount, see the GST before paying, then a full page load to `/pay`.
 * `onNeedDetails` opens the tax details form when the workspace has none yet.
 */
export function AddMoneyDialog({
	open,
	onOpenChange,
	onNeedDetails,
	suggested,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	onNeedDetails: (amount: number | null) => void;
	/** Pre-filled amount in paise (what is owed when the balance is below zero). */
	suggested?: number;
}) {
	const id = useId();
	const config = useQuery({ ...paymentConfigQuery, enabled: open });
	const [text, setText] = useState("");
	const [touched, setTouched] = useState(false);

	useEffect(() => {
		if (!open) return;
		setText(suggested && suggested > 0 ? String(suggested / 100) : "");
		setTouched(false);
	}, [open, suggested]);

	const c = config.data;
	const minor = parseMajor(text);
	const money = (n: number) => formatMinor(n, "INR");
	let error: string | undefined;
	if (minor === null || minor < 1) error = text.trim() === "" && !touched ? undefined : s.invalid;
	else if (c && minor < c.min_top_up_minor) error = fill(s.belowMin, { min: money(c.min_top_up_minor) });
	else if (c && minor > c.max_payment_minor) error = fill(s.aboveMax, { max: money(c.max_payment_minor) });
	const valid = minor !== null && minor >= 1 && !error && c?.enabled === true && c.tax_details_complete;
	const quote = useQuery({
		...quoteQuery("top_up", valid ? minor : null, null),
		enabled: valid === true,
	});

	const go = () => {
		setTouched(true);
		if (minor === null || error || !c) return;
		if (!c.tax_details_complete) {
			onOpenChange(false);
			onNeedDetails(minor);
			return;
		}
		goToPay({ purpose: "top_up", amount: minor });
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[480px]" data-testid="add-money-dialog">
				<form
					noValidate
					className="grid gap-4"
					onSubmit={(e) => {
						e.preventDefault();
						go();
					}}
				>
					<DialogHeader>
						<DialogTitle>{s.title}</DialogTitle>
						<DialogDescription>{s.lead}</DialogDescription>
					</DialogHeader>
					{c && !c.enabled ? <FormAlert tone="info">{s.off}</FormAlert> : null}
					{c?.enabled && !c.tax_details_complete ? <FormAlert tone="info">{s.needDetails}</FormAlert> : null}
					<fieldset className="m-0 grid gap-2 border-0 p-0">
						<legend className="mb-1 text-[13px] font-semibold">{s.quick}</legend>
						<div className="flex flex-wrap gap-2">
							{QUICK.map((q) => (
								<Button
									key={q}
									type="button"
									variant="outline"
									size="sm"
									aria-pressed={minor === q}
									onClick={() => {
										setText(String(q / 100));
										setTouched(true);
									}}
								>
									{money(q).replace(/\.00$/, "")}
								</Button>
							))}
						</div>
					</fieldset>
					<Field id={`${id}-amount`} label={s.amount} hint={s.amountHint} error={error}>
						<Input
							id={`${id}-amount`}
							inputMode="decimal"
							autoComplete="off"
							value={text}
							aria-invalid={error ? true : undefined}
							aria-describedby={error ? `${id}-amount-error` : `${id}-amount-hint`}
							onChange={(e) => {
								setText(e.target.value);
								setTouched(true);
							}}
						/>
					</Field>
					<output data-testid="add-money-preview" className="min-h-[1.4em] text-[14px] font-semibold">
						{quote.data ? previewLine(quote.data) : valid && quote.isFetching ? s.previewLoading : ""}
					</output>
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<Button type="submit" variant="default" disabled={c?.enabled === false}>
							{c && !c.tax_details_complete && c.enabled ? paymentStrings.pay.addTaxDetails : s.continue}
						</Button>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
