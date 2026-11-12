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
import type { TopUpRequestBody } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { parseMajor } from "@/lib/format/money";
import { planBillingStrings } from "@/lib/strings/billing-statements";
import { en } from "@/lib/strings/en";
import { useRequestTopUp } from "../billing/statements/api";

const s = planBillingStrings.topUp;

/**
 * "Request top-up" (BIL6): an optional amount and note that reach the team by e-mail, until a
 * payment gateway exists. One request an hour: asking again says when to try next.
 */
export function RequestTopUpDialog({
	open,
	onOpenChange,
	initialAmount = "",
	initialNote = "",
}: {
	open: boolean;
	onOpenChange: (open: boolean) => void;
	/** P6 plan choice: the amount a waiting plan change needs, and why, filled in for the owner. */
	initialAmount?: string;
	initialNote?: string;
}) {
	const id = useId();
	const request = useRequestTopUp();
	const [amount, setAmount] = useState("");
	const [note, setNote] = useState("");
	const [amountError, setAmountError] = useState<string | undefined>();
	const [failure, setFailure] = useState<string | null>(null);

	useEffect(() => {
		if (!open) return;
		setAmount(initialAmount);
		setNote(initialNote);
		setAmountError(undefined);
		setFailure(null);
	}, [open, initialAmount, initialNote]);

	const submit = async () => {
		setFailure(null);
		const body: TopUpRequestBody = {};
		if (amount.trim() !== "") {
			const minor = parseMajor(amount);
			if (minor === null || minor < 1) {
				setAmountError(s.errors.amount);
				return;
			}
			body.amount_minor = minor;
		}
		setAmountError(undefined);
		if (note.trim() !== "") body.note = note.trim();
		try {
			const res = await request.mutateAsync(body);
			if (res.sent) toast.success(s.sent, { description: s.sentBody });
			else toast(s.noted, { description: s.notedBody });
			onOpenChange(false);
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
		}
	};

	return (
		<Dialog open={open} onOpenChange={onOpenChange}>
			<DialogContent className="sm:max-w-[480px]">
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
					<Field id={`${id}-amount`} label={s.amount} hint={s.amountHint} error={amountError}>
						<Input
							id={`${id}-amount`}
							inputMode="decimal"
							autoComplete="off"
							value={amount}
							aria-invalid={amountError ? true : undefined}
							aria-describedby={amountError ? `${id}-amount-error` : `${id}-amount-hint`}
							onChange={(e) => setAmount(e.target.value)}
						/>
					</Field>
					<Field id={`${id}-note`} label={s.note} hint={s.noteHint}>
						<Textarea
							id={`${id}-note`}
							rows={3}
							maxLength={500}
							value={note}
							aria-describedby={`${id}-note-hint`}
							onChange={(e) => setNote(e.target.value)}
						/>
					</Field>
					<DialogFooter>
						<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
							{en.common.cancel}
						</Button>
						<SubmitButton variant="default" pending={request.isPending} pendingLabel={s.submitting}>
							{s.submit}
						</SubmitButton>
					</DialogFooter>
				</form>
			</DialogContent>
		</Dialog>
	);
}
