import { useId } from "react";
import { Field } from "@/components/auth/Field";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import { noSurprisesStrings } from "@/lib/strings/no-surprises";

const s = noSurprisesStrings.agreed;

export const MIN_REASON = 10;

export interface Agreement {
	agreed: boolean;
	reason: string;
}

export const NOT_AGREED: Agreement = { agreed: false, reason: "" };

/** The fields a request carries for an agreement; nothing when the business did not ask. */
export function agreementBody(a: Agreement): { business_agreed?: boolean; reason?: string } {
	return a.agreed ? { business_agreed: true, reason: a.reason.trim() } : {};
}

/** Whether an agreed change has the reason it needs (10 characters, once trimmed). */
export function agreementValid(a: Agreement): boolean {
	return !a.agreed || a.reason.trim().length >= MIN_REASON;
}

/**
 * "The business asked for this" (TV3): a change that costs a business more waits for its notice
 * period. If the owner asked for it, the provider applies it now and writes down how they asked;
 * the reason is kept in the audit log.
 */
export function AgreedField({
	value,
	onChange,
	showError,
}: {
	value: Agreement;
	onChange: (next: Agreement) => void;
	showError?: boolean;
}) {
	const id = useId();
	return (
		<div data-testid="agreed-field" className="grid gap-2">
			<div className="flex items-center gap-2.5">
				<Switch
					id={`${id}-on`}
					size="sm"
					checked={value.agreed}
					onCheckedChange={(on) => onChange({ ...value, agreed: on })}
				/>
				<label htmlFor={`${id}-on`} className="text-[13px] font-semibold">
					{s.toggle}
				</label>
			</div>
			<small className="pl-[42px] text-[12.5px] text-muted-foreground">{s.hint}</small>
			{value.agreed ? (
				<Field
					id={id}
					label={s.reason}
					hint={s.reasonHint}
					error={showError && !agreementValid(value) ? s.reasonError : null}
				>
					<Input
						id={id}
						value={value.reason}
						maxLength={500}
						onChange={(e) => onChange({ ...value, reason: e.target.value })}
						aria-describedby={`${id}-hint`}
					/>
				</Field>
			) : null}
		</div>
	);
}
