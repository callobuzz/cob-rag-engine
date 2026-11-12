import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
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
import { Textarea } from "@/components/ui/textarea";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date"; // P2 invoices
import { formatMinor, parseMajor } from "@/lib/format/money";
import { en, fill } from "@/lib/strings/en";
import { invoiceStrings } from "@/lib/strings/invoices"; // P2 invoices
import { paymentStrings } from "@/lib/strings/payments";
import { type PaymentDetail, useRefund } from "../api";

const s = paymentStrings.admin.refund;
/** The default confirmation limit of the engine (₹10,000): at or above it the amount is typed again. */
export const CONFIRM_ABOVE_MINOR = 1_000_000;
const money = (n: number) => formatMinor(n, "INR");

/**
 * Refund from the balance (P1): the amount (before GST) comes off the workspace's balance first, then
 * the payment service pays the customer back. Shows what can still be refunded; a large amount must
 * be typed again. One idempotency key per open dialog, so a double click is one refund.
 */
export function RefundDialog({
	payment,
	open,
	onOpenChange,
}: {
	payment: PaymentDetail;
	open: boolean;
	onOpenChange: (open: boolean) => void;
}) {
	const id = useId();
	const refund = useRefund(payment.id);
	const [amount, setAmount] = useState("");
	const [memo, setMemo] = useState("");
	const [confirm, setConfirm] = useState("");
	const [amountError, setAmountError] = useState<string | undefined>();
	const [failure, setFailure] = useState<string | null>(null);
	const [key, setKey] = useState(() => crypto.randomUUID());

	useEffect(() => {
		if (!open) return;
		setAmount("");
		setMemo("");
		setConfirm("");
		setAmountError(undefined);
		setFailure(null);
		setKey(crypto.randomUUID());
	}, [open]);

	const minor = parseMajor(amount);
	const needsConfirm = minor !== null && minor >= CONFIRM_ABOVE_MINOR;

	const submit = async () => {
		setFailure(null);
		if (minor === null || minor < 1) {
			setAmountError(s.invalid);
			return;
		}
		if (minor > payment.refundable_minor) {
			setAmountError(fill(s.tooMuch, { max: money(payment.refundable_minor) }));
			return;
		}
		setAmountError(undefined);
		try {
			const res = await refund.mutateAsync({
				amount_minor: minor,
				memo: memo.trim() === "" ? "Refund" : memo.trim(),
				confirm_amount_minor: needsConfirm ? (parseMajor(confirm) ?? undefined) : undefined,
				idempotency_key: key,
			});
			toast.success(s.done, { description: fill(s.doneBody, { gross: money(res.gross_minor) }) });
			onOpenChange(false);
		} catch (e) {
			const err = toApiError(e);
			if (isApiError(err) && err.code === "refund_failed") setFailure(s.failed);
			else if (isApiError(err) && err.code === "refund_exceeds_refundable")
				setAmountError(fill(s.tooMuch, { max: money(Number(err.extra.refundable_minor ?? 0)) }));
			else setFailure(messageFor(err).title);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[480px]" data-testid="refund-dialog">
				<form
					noValidate
					className="grid gap-4"
					onSubmit={(e) => {
						e.preventDefault();
						void submit();
					}}
				>
					<DialogHeader>
						<DialogTitle>{s.title}</DialogTitle>
						<DialogDescription>{s.lead}</DialogDescription>
					</DialogHeader>
					{failure ? <FormAlert>{failure}</FormAlert> : null}
					{/* P2 invoices */}
					{payment.credit_note && !payment.credit_note.possible ? (
						<p
							data-testid="refund-no-credit-note"
							className="m-0 rounded-md border border-border bg-secondary px-3 py-2 text-[12.5px]"
						>
							{fill(invoiceStrings.refund.noCreditNote, {
								date: payment.credit_note.deadline ? formatDate(payment.credit_note.deadline) : "",
							})}
						</p>
					) : payment.credit_note?.invoice_number ? (
						<p data-testid="refund-credit-note" className="m-0 text-[12.5px] text-muted-foreground">
							{invoiceStrings.refund.creditNoteNote}
						</p>
					) : null}
					{/* /P2 invoices */}
					<Field
						id={`${id}-amount`}
						label={s.amount}
						hint={fill(s.amountHint, { max: money(payment.refundable_minor) })}
						error={amountError}
					>
						<Input
							id={`${id}-amount`}
							inputMode="decimal"
							autoComplete="off"
							value={amount}
							aria-invalid={amountError ? true : undefined}
							onChange={(e) => setAmount(e.target.value)}
						/>
					</Field>
					{needsConfirm ? (
						<Field id={`${id}-confirm`} label={s.confirm}>
							<Input
								id={`${id}-confirm`}
								inputMode="decimal"
								autoComplete="off"
								value={confirm}
								onChange={(e) => setConfirm(e.target.value)}
							/>
						</Field>
					) : null}
					<Field id={`${id}-memo`} label={s.memo} hint={s.memoHint}>
						<Textarea
							id={`${id}-memo`}
							rows={2}
							maxLength={500}
							value={memo}
							onChange={(e) => setMemo(e.target.value)}
						/>
					</Field>
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={refund.isPending} pendingLabel={s.submitting}>
							{s.submit}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
