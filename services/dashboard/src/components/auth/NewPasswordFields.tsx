import { Check, Circle, X } from "lucide-react";
import { useId } from "react";
import { type FieldValues, type Path, type UseFormReturn, useFormState, useWatch } from "react-hook-form";
import { z } from "zod";
import { weakPasswordReason } from "@/lib/api/error-messages";
import { en, fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";
import { describedBy, Field } from "./Field";
import { PasswordField } from "./PasswordField";
import { PasswordMeter, passwordLengthOk } from "./PasswordMeter";

export type PasswordPolicy = { min_length: number; max_length: number };

/** Form values every new-password form carries. */
export interface NewPasswordValues {
	password: string;
	repeat: string;
}

/** What the engine rejects for the e-mail rule: the local part (4+ characters) inside the password. */
export function emailNamePart(email: string | undefined): string | null {
	const local = (email ?? "").normalize("NFKC").trim().toLowerCase().split("@")[0] ?? "";
	return local.length >= 4 ? local : null;
}

export function containsEmailName(password: string, email: string | undefined): boolean {
	const part = emailNamePart(email);
	return !!part && password.normalize("NFKC").toLowerCase().includes(part);
}

/** Zod shape for the two password fields; `withPasswordRules` adds the cross-field rules. */
export function passwordShape(policy: PasswordPolicy) {
	return {
		password: z.string().refine((pw) => passwordLengthOk(pw, policy), {
			message: `Use ${policy.min_length} to ${policy.max_length} characters.`,
		}),
		repeat: z.string(),
	};
}

/**
 * The rules that need two fields: "repeat" equals "password" (when the schema has one) and the
 * password does not contain the e-mail's name part (when the e-mail is known). The engine stays the
 * source of truth (it also rejects the 10,000 most common passwords); these are what the browser can
 * check, so the person sees the problem while typing instead of after a round trip.
 */
export function withPasswordRules<S extends z.ZodObject>(
	schema: S,
	opts: { email?: (values: Record<string, unknown>) => string | undefined; repeat?: boolean } = {},
): S {
	const { email, repeat = true } = opts;
	return schema.superRefine((v, ctx) => {
		const values = v as Record<string, unknown>;
		const password = typeof values.password === "string" ? values.password : "";
		if (containsEmailName(password, email?.(values))) {
			ctx.addIssue({ code: "custom", path: ["password"], message: weakPasswordReason("contains_email") });
		}
		if (repeat && typeof values.repeat === "string" && values.repeat !== password) {
			ctx.addIssue({ code: "custom", path: ["repeat"], message: en.reset.mismatch });
		}
	}) as unknown as S;
}

type Requirement = { key: string; label: string; met: boolean; pending?: boolean };

export function passwordRequirements(password: string, policy: PasswordPolicy, email?: string): Requirement[] {
	const list: Requirement[] = [
		{
			key: "length",
			label: fill(en.password.reqLength, { min: policy.min_length, max: policy.max_length }),
			met: passwordLengthOk(password, policy),
		},
	];
	if (emailNamePart(email)) {
		list.push({ key: "email", label: en.password.reqEmail, met: !containsEmailName(password, email) });
	}
	return list;
}

/**
 * Live checklist of what the browser can check. The visible list is plain content (browse it any
 * time); a separate polite status region carries one sentence that only changes when a requirement
 * flips, so a screen reader hears state changes, not every keystroke.
 */
export function PasswordChecklist({
	id,
	password,
	policy,
	email,
	showFailures,
}: {
	id: string;
	password: string;
	policy: PasswordPolicy;
	email?: string;
	showFailures: boolean;
}) {
	const reqs = passwordRequirements(password, policy, email);
	const typed = password.length > 0;
	const unmet = reqs.filter((r) => !r.met);
	const summary = !typed
		? ""
		: unmet.length
			? fill(en.password.stillNeeded, { list: unmet.map((r) => r.label).join(", ") })
			: en.password.allMet;
	return (
		<div id={id} data-testid="password-checklist">
			<p className="m-0 mb-1 text-[12.5px] text-muted-foreground">{en.password.requirements}</p>
			<ul className="m-0 grid list-none gap-0.5 p-0 text-[12.5px]">
				{reqs.map((r) => {
					const failed = typed && !r.met && showFailures;
					const Icon = r.met && typed ? Check : failed ? X : Circle;
					return (
						<li
							key={r.key}
							data-met={r.met && typed ? "true" : "false"}
							className={cn(
								"flex items-center gap-1.5",
								r.met && typed ? "text-success" : failed ? "text-destructive" : "text-muted-foreground",
							)}
						>
							<Icon className="size-3.5 shrink-0" aria-hidden />
							<span>{r.label}</span>
							<span className="sr-only">{r.met && typed ? " (done)" : " (not yet)"}</span>
						</li>
					);
				})}
				<li className="flex items-center gap-1.5 text-muted-foreground">
					<Circle className="size-3.5 shrink-0 opacity-40" aria-hidden />
					<span>{en.password.reqCommon}</span>
				</li>
			</ul>
			<div role="status" aria-live="polite" aria-atomic="true" className="sr-only">
				{summary}
			</div>
		</div>
	);
}

/**
 * New password (+ repeat) fields for a form whose values include `password` and `repeat`
 * (`NewPasswordValues`). Live: the checklist and the meter follow every keystroke; "Repeat" says
 * "Passwords match" or "Passwords don't match" from its first blur on and clears the moment they
 * agree. Errors are linked with aria-describedby and aria-invalid.
 */
export function NewPasswordFields<T extends FieldValues>({
	form,
	policy,
	email,
	label = en.reset.newPassword,
	repeatLabel = en.reset.repeat,
	withRepeat = true,
}: {
	// biome-ignore lint/suspicious/noExplicitAny: any form context and output type
	form: UseFormReturn<T, any, any>;
	policy: PasswordPolicy;
	/** The address the password must not contain; leave out when it is not known. */
	email?: string;
	label?: string;
	repeatLabel?: string;
	withRepeat?: boolean;
}) {
	const ids = { password: useId(), repeat: useId(), meter: useId(), list: useId() };
	const f = form as unknown as UseFormReturn<FieldValues>;
	const [password = "", repeat = ""] = useWatch({ control: f.control, name: ["password", "repeat"] }) as [
		string?,
		string?,
	];
	const { errors, touchedFields, isSubmitted } = useFormState({ control: f.control, name: ["password", "repeat"] });
	const pwError = errors.password?.message as string | undefined;
	const repeatError = errors.repeat?.message as string | undefined;
	const repeatSeen = !!touchedFields.repeat || isSubmitted;
	const matches = repeat !== "" && repeat === password;
	const repeatStatus = !withRepeat || !repeatSeen ? "" : (repeatError ?? (matches ? en.reset.match : ""));

	return (
		<>
			<Field id={ids.password} label={label} error={pwError}>
				<PasswordField
					id={ids.password}
					autoComplete="new-password"
					aria-invalid={!!pwError || undefined}
					aria-describedby={[describedBy(ids.password, { error: !!pwError }), ids.list, ids.meter]
						.filter(Boolean)
						.join(" ")}
					{...f.register("password" as Path<FieldValues>, {
						// A repeat that already has an opinion follows the password as it is edited.
						onChange: () => {
							if (withRepeat && (f.getFieldState("repeat").isTouched || f.formState.isSubmitted)) {
								void f.trigger("repeat");
							}
						},
					})}
				/>
			</Field>
			<PasswordChecklist
				id={ids.list}
				password={password}
				policy={policy}
				email={email}
				showFailures={!!pwError || isSubmitted}
			/>
			<PasswordMeter id={ids.meter} password={password} policy={policy} />
			{withRepeat ? (
				<div className="grid content-start gap-1.5">
					<label htmlFor={ids.repeat} className="text-[13px] font-semibold">
						{repeatLabel}
					</label>
					<PasswordField
						id={ids.repeat}
						autoComplete="new-password"
						aria-invalid={!!repeatError || undefined}
						aria-describedby={`${ids.repeat}-status`}
						{...f.register("repeat" as Path<FieldValues>)}
					/>
					<small
						id={`${ids.repeat}-status`}
						role="status"
						aria-live="polite"
						data-testid="repeat-status"
						className={cn("min-h-[1.2em] text-[12.5px] font-medium", repeatError ? "text-destructive" : "text-success")}
					>
						{repeatStatus}
					</small>
				</div>
			) : null}
		</>
	);
}
