import { type ClipboardEvent, type FocusEvent, type KeyboardEvent, type Ref, useEffect, useRef } from "react";
import { cn } from "@/lib/utils";

/**
 * Six numeric boxes (mockup `.code`). Paste of 6 digits fills all, backspace moves back, arrows
 * move, `autocomplete="one-time-code"` on the first box only, each box labelled "Digit n of 6",
 * grouped with role="group". `onComplete` fires when the sixth digit arrives (auto-submit).
 */
export const CODE_LENGTH = 6;

export function CodeInput({
	value,
	onChange,
	onComplete,
	invalid = false,
	disabled = false,
	label = "Verification code",
	describedBy,
	focusSignal = 0,
	autoFocus = false,
	onBlur,
	inputRef,
}: {
	value: string;
	onChange: (value: string) => void;
	onComplete?: (value: string) => void;
	invalid?: boolean;
	disabled?: boolean;
	label?: string;
	describedBy?: string;
	/** Change this number to clear focus back to the first box (e.g. after a wrong code). */
	focusSignal?: number;
	autoFocus?: boolean;
	/** Fires when focus leaves the whole group (not when it moves between boxes): marks it touched. */
	onBlur?: () => void;
	/** Ref to the first box (react-hook-form `field.ref`, so a failed submit can focus the code). */
	inputRef?: Ref<HTMLInputElement>;
}) {
	const refs = useRef<(HTMLInputElement | null)[]>([]);
	const leaveGroup = (e: FocusEvent<HTMLDivElement>) => {
		if (!e.currentTarget.contains(e.relatedTarget)) onBlur?.();
	};
	const digits = Array.from({ length: CODE_LENGTH }, (_, i) => value[i] ?? "");

	useEffect(() => {
		if (focusSignal > 0) refs.current[0]?.focus();
	}, [focusSignal]);

	const setAt = (index: number, digit: string) => {
		const next = digits.slice();
		next[index] = digit;
		const joined = next.join("").slice(0, CODE_LENGTH);
		onChange(joined);
		if (joined.length === CODE_LENGTH && /^\d{6}$/.test(joined)) onComplete?.(joined);
	};

	const fillFrom = (index: number, text: string) => {
		const clean = text.replace(/\D/g, "");
		if (!clean) return;
		const next = digits.slice();
		let i = index;
		for (const ch of clean) {
			if (i >= CODE_LENGTH) break;
			next[i] = ch;
			i++;
		}
		const joined = next.join("");
		onChange(joined);
		refs.current[Math.min(i, CODE_LENGTH - 1)]?.focus();
		if (/^\d{6}$/.test(joined)) onComplete?.(joined);
	};

	const onKeyDown = (index: number, e: KeyboardEvent<HTMLInputElement>) => {
		if (e.key === "Backspace") {
			e.preventDefault();
			if (digits[index]) {
				setAt(index, "");
			} else if (index > 0) {
				setAt(index - 1, "");
				refs.current[index - 1]?.focus();
			}
		} else if (e.key === "ArrowLeft" && index > 0) {
			e.preventDefault();
			refs.current[index - 1]?.focus();
		} else if (e.key === "ArrowRight" && index < CODE_LENGTH - 1) {
			e.preventDefault();
			refs.current[index + 1]?.focus();
		}
	};

	const onPaste = (index: number, e: ClipboardEvent<HTMLInputElement>) => {
		e.preventDefault();
		fillFrom(index, e.clipboardData.getData("text"));
	};

	return (
		// biome-ignore lint/a11y/useSemanticElements: a fieldset would add a legend and default border; role=group is the spec'd pattern
		<div
			role="group"
			aria-label={label}
			aria-describedby={describedBy}
			onBlur={leaveGroup}
			className={cn("grid grid-cols-6 gap-2", invalid && "animate-shake")}
		>
			{digits.map((d, i) => (
				<input
					// biome-ignore lint/suspicious/noArrayIndexKey: fixed six positions
					key={i}
					ref={(el) => {
						refs.current[i] = el;
						if (i === 0 && inputRef) {
							if (typeof inputRef === "function") inputRef(el);
							else (inputRef as { current: HTMLInputElement | null }).current = el;
						}
					}}
					value={d}
					disabled={disabled}
					inputMode="numeric"
					pattern="[0-9]*"
					maxLength={CODE_LENGTH}
					autoComplete={i === 0 ? "one-time-code" : "off"}
					// biome-ignore lint/a11y/noAutofocus: the code is the only task on the page
					autoFocus={autoFocus && i === 0}
					aria-label={`Digit ${i + 1} of ${CODE_LENGTH}`}
					aria-invalid={invalid || undefined}
					onChange={(e) => {
						const v = e.target.value.replace(/\D/g, "");
						if (v.length > 1) {
							fillFrom(i, v);
							return;
						}
						setAt(i, v);
						if (v && i < CODE_LENGTH - 1) refs.current[i + 1]?.focus();
					}}
					onKeyDown={(e) => onKeyDown(i, e)}
					onPaste={(e) => onPaste(i, e)}
					onFocus={(e) => e.currentTarget.select()}
					className="h-[52px] w-full min-w-0 rounded-sm border border-input bg-muted text-center font-mono text-[22px] font-semibold outline-none focus-visible:border-brand-text focus-visible:shadow-[0_0_0_3px_var(--brand-soft)] aria-invalid:border-destructive"
				/>
			))}
		</div>
	);
}
