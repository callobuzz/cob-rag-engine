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
import { Textarea } from "@/components/ui/textarea";
import { tenantPlanQuery } from "@/features/customers/api";
import type { AddOnPayment } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { type Currency, formatMinor } from "@/lib/format/money";
import { addOnStrings as s } from "@/lib/strings/billing-addons";
import { en, fill } from "@/lib/strings/en";
import { addOnsQuery, useApplyAddOn } from "./api";
import { addOnSummary, applyEffect } from "./summary";

/** A new random key each time the dialog opens: a double click (or a retry) is one grant. */
const newKey = (): string => crypto.randomUUID();

/**
 * "Apply an add-on" (BIL5): pick one, say how it was paid, add a note. Shows what will happen
 * before it does ("Documents 100 → 150 until 1 Nov", "+₹1,100.00 credit, expires 3 Jan") and
 * warns when the customer's limit is already unlimited. Only active add-ons priced in the
 * customer's currency are offered.
 */
export function ApplyAddOnDialog({
	open,
	onOpenChange,
	tenantId,
	tenantName,
	currency,
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	tenantId: string;
	tenantName: string;
	currency: Currency;
}) {
	const baseId = useId();
	const catalogue = useQuery({ ...addOnsQuery(false), enabled: open });
	const plan = useQuery({ ...tenantPlanQuery(tenantId), enabled: open });
	const apply = useApplyAddOn(tenantId);
	const [addOnId, setAddOnId] = useState("");
	const [payment, setPayment] = useState<AddOnPayment>("external");
	const [memo, setMemo] = useState("");
	const [pickError, setPickError] = useState<string | null>(null);
	const [failure, setFailure] = useState<string | null>(null);
	const [key, setKey] = useState(newKey);

	useEffect(() => {
		if (!open) return;
		setAddOnId("");
		setPayment("external");
		setMemo("");
		setPickError(null);
		setFailure(null);
		setKey(newKey());
	}, [open]);

	const offered = (catalogue.data?.items ?? []).filter((a) => a.currency === currency);
	const chosen = offered.find((a) => a.id === addOnId);
	const walletBlocked = chosen?.kind === "credit_pack";
	const effective = walletBlocked && payment === "wallet" ? "external" : payment;
	const effect = chosen ? applyEffect(chosen, plan.data?.limits) : null;

	const submit = async () => {
		setFailure(null);
		if (!chosen) {
			setPickError(s.apply.errors.pick);
			return;
		}
		try {
			await apply.mutateAsync({
				add_on_id: chosen.id,
				payment: effective,
				idempotency_key: key,
				...(memo.trim() ? { memo: memo.trim() } : {}),
			});
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
			return;
		}
		toast.success(s.apply.applied, {
			description: fill(s.apply.appliedBody, { add_on: chosen.name, name: tenantName }),
		});
		onOpenChange(false);
	};

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
						<DialogTitle>{fill(s.apply.title, { name: tenantName })}</DialogTitle>
						<DialogDescription>{s.apply.lead}</DialogDescription>
					</DialogHeader>
					{failure ? <FormAlert>{failure}</FormAlert> : null}
					{catalogue.isSuccess && offered.length === 0 ? (
						<p className="m-0 text-foreground-2">{fill(s.apply.noneInCurrency, { currency })}</p>
					) : (
						<Field id={`${baseId}-pick`} label={s.apply.pick} error={pickError}>
							<NativeSelect
								id={`${baseId}-pick`}
								value={addOnId}
								aria-invalid={pickError ? true : undefined}
								aria-describedby={pickError ? `${baseId}-pick-error` : undefined}
								onChange={(e) => {
									setAddOnId(e.target.value);
									setPickError(null);
								}}
							>
								<option value="">{s.apply.pickPlaceholder}</option>
								{offered.map((a) => (
									<option key={a.id} value={a.id}>
										{`${a.name} (${addOnSummary(a)}${a.price_minor ? `, ${formatMinor(a.price_minor, currency)}` : ""})`}
									</option>
								))}
							</NativeSelect>
						</Field>
					)}
					<Field id={`${baseId}-payment`} label={s.apply.payment} hint={walletBlocked ? s.apply.walletNote : undefined}>
						<NativeSelect
							id={`${baseId}-payment`}
							value={effective}
							onChange={(e) => setPayment(e.target.value as AddOnPayment)}
						>
							<option value="external">{s.apply.payments.external}</option>
							<option value="free">{s.apply.payments.free}</option>
							<option value="wallet" disabled={walletBlocked}>
								{s.apply.payments.wallet}
							</option>
						</NativeSelect>
					</Field>
					<Field id={`${baseId}-memo`} label={s.apply.memo} hint={s.apply.memoHint}>
						<Textarea
							id={`${baseId}-memo`}
							rows={2}
							maxLength={500}
							value={memo}
							onChange={(e) => setMemo(e.target.value)}
						/>
					</Field>
					{effect && chosen ? (
						<div
							data-testid="apply-effect"
							className="grid gap-1 rounded-md border border-border bg-secondary px-3 py-2 text-[13.5px]"
						>
							<b>{s.apply.effect}</b>
							<span>{effect.text}</span>
							{effective === "wallet" && chosen.price_minor > 0 ? (
								<span>{fill(s.apply.effectPaid, { price: formatMinor(chosen.price_minor, currency) })}</span>
							) : null}
						</div>
					) : null}
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={apply.isPending} pendingLabel={s.apply.submitting}>
							{s.apply.submit}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
