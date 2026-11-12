import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
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
import type { ManualEntryRequest } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { type Currency, formatMinor, parseMajor } from "@/lib/format/money";
import { ledgerStrings as s } from "@/lib/strings/billing";
import { en, fill } from "@/lib/strings/en";
import { useAddLedgerEntry } from "./api";

type Kind = "top_up" | "adjustment" | "refund";

interface Draft {
	kind: Kind;
	amount: string;
	note: string;
	description: string;
	expires: string;
}

const EMPTY: Draft = { kind: "top_up", amount: "", note: "", description: "", expires: "" };

/** A new random key each time the dialog opens: a double click (or a retry) is one entry. */
const newKey = (): string => crypto.randomUUID();

type Errors = Partial<Record<"amount" | "note" | "expires", string>>;

function validate(d: Draft): { errors: Errors; minor: number | null } {
	const errors: Errors = {};
	const minor = parseMajor(d.amount);
	if (minor === null) errors.amount = s.dialog.errors.amount;
	else if (minor === 0) errors.amount = s.dialog.errors.amountZero;
	else if (d.kind !== "adjustment" && minor < 0) errors.amount = s.dialog.errors.amountSign;
	if (d.kind !== "top_up" && d.note.trim() === "") errors.note = s.dialog.errors.note;
	if (d.expires && Date.parse(`${d.expires}T23:59:59`) <= Date.now()) errors.expires = s.dialog.errors.expires;
	return { errors, minor };
}

/**
 * "Add a transaction" (BIL3): a top-up, an adjustment or a refund for one customer. Each opening
 * mints one idempotency key, so a double click is one entry. A large amount is refused by the
 * server until the amount is typed again; this dialog then asks for exactly that, showing the
 * formatted amount, and re-sends with `confirm_amount_minor`.
 */
export function AddTransactionDialog({
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
	const add = useAddLedgerEntry(tenantId);
	const [draft, setDraft] = useState<Draft>(EMPTY);
	const [errors, setErrors] = useState<Errors>({});
	const [failure, setFailure] = useState<string | null>(null);
	const [key, setKey] = useState(newKey);
	const [confirming, setConfirming] = useState<{ minor: number; error: string | null } | null>(null);

	useEffect(() => {
		if (!open) return;
		setDraft(EMPTY);
		setErrors({});
		setFailure(null);
		setConfirming(null);
		setKey(newKey());
	}, [open]);

	const set = <K extends keyof Draft>(k: K, v: Draft[K]) => setDraft((d) => ({ ...d, [k]: v }));
	const canExpire = draft.kind === "adjustment" && (parseMajor(draft.amount) ?? 0) > 0;

	const send = async (minor: number, confirmMinor: number | null) => {
		const body: ManualEntryRequest = {
			entry_type: draft.kind,
			amount_minor: minor,
			idempotency_key: key,
		};
		if (draft.note.trim()) body.memo = draft.note.trim();
		if (draft.description.trim()) body.memo_public = draft.description.trim();
		if (confirmMinor !== null) body.confirm_amount_minor = confirmMinor;
		if (canExpire && draft.expires) body.expires_at = new Date(`${draft.expires}T23:59:59`).toISOString();
		try {
			await add.mutateAsync(body);
		} catch (e) {
			const err = toApiError(e);
			if (isApiError(err) && err.code === "confirm_mismatch") {
				setConfirming({ minor, error: confirmMinor === null ? null : s.dialog.confirm.mismatch });
				return;
			}
			setConfirming(null);
			setFailure(messageFor(err).title);
			return;
		}
		toast.success(s.dialog.added, {
			description: fill(s.dialog.addedBody, {
				type: s.types[draft.kind],
				amount: formatMinor(Math.abs(minor), currency),
				name: tenantName,
			}),
		});
		setConfirming(null);
		onOpenChange(false);
	};

	const submit = () => {
		setFailure(null);
		const { errors: found, minor } = validate(draft);
		setErrors(found);
		if (Object.keys(found).length > 0 || minor === null) return;
		void send(minor, null);
	};

	return (
		<>
			<Dialog open={open && confirming === null} onOpenChange={onOpenChange}>
				<DialogContent className="max-h-[92vh] overflow-y-auto sm:max-w-[520px]">
					<form
						noValidate
						className="grid gap-4"
						onSubmit={(e) => {
							e.preventDefault();
							submit();
						}}
					>
						<DialogHeader>
							<DialogTitle>{s.dialog.title}</DialogTitle>
							<DialogDescription>{fill(s.dialog.lead, { currency })}</DialogDescription>
						</DialogHeader>
						{failure ? <FormAlert>{failure}</FormAlert> : null}
						<Field id={`${baseId}-type`} label={s.dialog.type}>
							<NativeSelect
								id={`${baseId}-type`}
								value={draft.kind}
								onChange={(e) => set("kind", e.target.value as Kind)}
							>
								<option value="top_up">{s.dialog.types.top_up}</option>
								<option value="adjustment">{s.dialog.types.adjustment}</option>
								<option value="refund">{s.dialog.types.refund}</option>
							</NativeSelect>
						</Field>
						<Field
							id={`${baseId}-amount`}
							label={s.dialog.amount}
							hint={draft.kind === "adjustment" ? s.dialog.amountHint : undefined}
							error={errors.amount}
						>
							<Input
								id={`${baseId}-amount`}
								inputMode="decimal"
								autoComplete="off"
								value={draft.amount}
								aria-invalid={errors.amount ? true : undefined}
								aria-describedby={errors.amount ? `${baseId}-amount-error` : undefined}
								onChange={(e) => set("amount", e.target.value)}
							/>
						</Field>
						<Field id={`${baseId}-note`} label={s.dialog.note} hint={s.dialog.noteHint} error={errors.note}>
							<Textarea
								id={`${baseId}-note`}
								rows={2}
								maxLength={500}
								value={draft.note}
								aria-invalid={errors.note ? true : undefined}
								aria-describedby={errors.note ? `${baseId}-note-error` : `${baseId}-note-hint`}
								onChange={(e) => set("note", e.target.value)}
							/>
						</Field>
						<Field id={`${baseId}-description`} label={s.dialog.description} hint={s.dialog.descriptionHint}>
							<Input
								id={`${baseId}-description`}
								maxLength={200}
								value={draft.description}
								aria-describedby={`${baseId}-description-hint`}
								onChange={(e) => set("description", e.target.value)}
							/>
						</Field>
						{canExpire ? (
							<Field
								id={`${baseId}-expires`}
								label={s.dialog.expires}
								hint={s.dialog.expiresHint}
								error={errors.expires}
							>
								<Input
									id={`${baseId}-expires`}
									type="date"
									value={draft.expires}
									aria-invalid={errors.expires ? true : undefined}
									aria-describedby={errors.expires ? `${baseId}-expires-error` : `${baseId}-expires-hint`}
									onChange={(e) => set("expires", e.target.value)}
								/>
							</Field>
						) : null}
						<DialogFooter>
							<Button type="button" variant="ghost" onClick={() => onOpenChange(false)}>
								{en.common.cancel}
							</Button>
							<SubmitButton variant="default" pending={add.isPending} pendingLabel={s.dialog.submitting}>
								{s.dialog.submit}
							</SubmitButton>
						</DialogFooter>
					</form>
				</DialogContent>
			</Dialog>
			{confirming ? (
				<TypedConfirmDialog
					open
					onOpenChange={(o) => {
						if (!o) setConfirming(null);
					}}
					title={s.dialog.confirm.title}
					body={fill(s.dialog.confirm.body, {
						amount: formatMinor(Math.abs(confirming.minor), currency),
						name: tenantName,
					})}
					word={formatMinor(Math.abs(confirming.minor), currency)}
					confirmLabel={fill(s.dialog.confirm.action, { amount: formatMinor(Math.abs(confirming.minor), currency) })}
					pending={add.isPending}
					error={confirming.error}
					onConfirm={() => void send(confirming.minor, confirming.minor)}
				/>
			) : null}
		</>
	);
}
