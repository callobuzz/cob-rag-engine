import { type BaseSyntheticEvent, type MutableRefObject, useCallback, useEffect, useRef } from "react";
import { type FieldValues, get, type Path, type UseFormProps, type UseFormReturn, useForm } from "react-hook-form";
import { useApiErrorState } from "@/components/auth/ErrorAlert";
import { weakPasswordReason } from "@/lib/api/error-messages";
import { type ApiError, toApiError } from "@/lib/api/errors";

/**
 * The one place that decides how every dashboard form validates (GOV.UK / NN/g / WAI inline
 * validation):
 *  - `mode: "onTouched"`: a field is validated the first time it loses focus, never while the
 *    person is still typing their first answer;
 *  - `reValidateMode: "onChange"`: once a field shows an error (or the form was submitted) every
 *    keystroke re-validates it, so the message goes away the moment the input becomes valid;
 *  - `shouldFocusError`: a failed submit moves focus to the first invalid field.
 * Callers cannot override these; a form with different needs is a design question, not a prop.
 */
export const FORM_VALIDATION = {
	mode: "onTouched",
	reValidateMode: "onChange",
	shouldFocusError: true,
} as const;

export function useAppForm<TFieldValues extends FieldValues, TContext = unknown, TTransformedValues = TFieldValues>(
	props: UseFormProps<TFieldValues, TContext, TTransformedValues> = {},
) {
	return useForm<TFieldValues, TContext, TTransformedValues>({ ...props, ...FORM_VALIDATION });
}

/** Focus the first control marked `aria-invalid="true"` (custom controls RHF has no ref for). */
export function focusFirstInvalid(root: ParentNode | null): void {
	setTimeout(() => {
		const scope = root ?? document;
		const active = document.activeElement;
		if (active instanceof HTMLElement && active.getAttribute("aria-invalid") === "true") return;
		scope.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus();
	}, 0);
}

export interface AppSubmitOptions<V extends FieldValues> {
	/** Form field for an API error path (`llm_config.model` → `model`); null skips it. Default: same name. */
	fieldName?: (path: string) => Path<V> | null;
	/** The field a `weak_password` reasons message belongs to. Default `"password"`. */
	passwordField?: Path<V>;
	/** Password policy for the `weak_password` wording. */
	passwordPolicy?: { min_length: number; max_length: number };
	/** See the error first. Return true when it was handled (no form-level alert is shown). */
	onError?: (err: ApiError) => boolean | undefined;
}

type AnyForm<V extends FieldValues> = UseFormReturn<V, unknown, FieldValues>;

function hasField<V extends FieldValues>(form: AnyForm<V>, name: string): boolean {
	return get(form.getValues(), name) !== undefined;
}

/**
 * Put an API error on the fields it names. True when at least one field took it (the caller then
 * shows no form-level alert); false leaves it for the alert.
 */
export function applyApiError<V extends FieldValues>(
	form: AnyForm<V>,
	err: ApiError,
	opts: AppSubmitOptions<V> = {},
): boolean {
	const set = (name: Path<V>, message: string) => form.setError(name, { type: "server", message });
	let first: Path<V> | null = null;
	if (err.code === "weak_password") {
		const name = opts.passwordField ?? ("password" as Path<V>);
		if (hasField(form, name)) {
			const reasons = Array.isArray(err.extra.reasons) ? err.extra.reasons.map(String) : [];
			set(
				name,
				reasons.map((r) => weakPasswordReason(r, opts.passwordPolicy)).join(" ") || "Choose a stronger password.",
			);
			first = name;
		}
	}
	for (const fe of err.fieldErrors) {
		const name = opts.fieldName ? opts.fieldName(fe.path) : (fe.path as Path<V>);
		if (!name || !hasField(form, name)) continue;
		set(name, fe.message);
		first ??= name;
	}
	if (first) {
		form.setFocus(first);
		return true;
	}
	return false;
}

/**
 * Submit plumbing shared by every form: wraps `handleSubmit`, keeps the form-level server alert and
 * puts field-level server errors on their fields.
 *
 *  - `run(values)` does the request and THROWS on failure (an `ApiError` or anything `toApiError`
 *    accepts); a field-level error (`weak_password`, 422 `loc`, your `onError`) lands on its field,
 *    anything else becomes the form alert (`error`, with a Retry-After deadline in `until`);
 *  - a field's server error clears as soon as that field is edited; the alert clears as soon as
 *    ANY field is edited and at the start of the next submit, so nothing lingers after a retry;
 *  - a second submit while one is running is ignored (no double requests);
 *  - an invalid submit focuses the first invalid control.
 */
export function useAppSubmit<V extends FieldValues, TContext = unknown, TTransformed extends FieldValues = V>(
	form: UseFormReturn<V, TContext, TTransformed>,
	run: (values: TTransformed, event?: BaseSyntheticEvent) => Promise<unknown> | unknown,
	opts: AppSubmitOptions<V> = {},
) {
	const alert = useApiErrorState();
	const busy: MutableRefObject<boolean> = useRef(false);
	const runRef = useRef(run);
	runRef.current = run;
	const optsRef = useRef(opts);
	optsRef.current = opts;
	const { clear, set: setAlert, locked } = alert;

	useEffect(() => {
		const sub = form.watch((_values, info) => {
			if (info.type !== "change") return;
			clear();
			const name = info.name as Path<V> | undefined;
			if (name && form.getFieldState(name).error?.type === "server") form.clearErrors(name);
		});
		return () => sub.unsubscribe();
	}, [form, clear]);

	const onSubmit = useCallback(
		async (event?: BaseSyntheticEvent): Promise<void> => {
			event?.preventDefault?.();
			if (busy.current || locked) return;
			const root = (event?.currentTarget as HTMLElement | undefined) ?? null;
			busy.current = true;
			clear();
			try {
				await form.handleSubmit(
					async (values) => {
						try {
							await runRef.current(values, event);
						} catch (e) {
							const err = toApiError(e);
							if (optsRef.current.onError?.(err) === true) return;
							if (applyApiError(form as unknown as AnyForm<V>, err, optsRef.current)) return;
							setAlert(err);
						}
					},
					() => focusFirstInvalid(root),
				)(event);
			} finally {
				busy.current = false;
			}
		},
		[form, clear, setAlert, locked],
	);

	return {
		onSubmit,
		error: alert.error,
		until: alert.until,
		locked: alert.locked,
		clearError: clear,
		setError: setAlert,
		isSubmitting: form.formState.isSubmitting,
	};
}
