import { LoaderCircle } from "lucide-react";
import type { ComponentProps, MouseEvent } from "react";
import { Button } from "@/components/ui/button";

/**
 * The submit button of every form. It is never disabled for being invalid (the errors say what to
 * fix); while a request runs it shows a spinner, announces itself busy, and swallows clicks so the
 * form cannot be submitted twice. `locked` is a Retry-After lockout. `aria-disabled` rather than
 * `disabled` keeps focus on the button, so keyboard and screen-reader users do not lose their place.
 */
export function SubmitButton({
	pending = false,
	locked = false,
	pendingLabel,
	children,
	onClick,
	"aria-disabled": ariaDisabled,
	...props
}: Omit<ComponentProps<typeof Button>, "type"> & {
	pending?: boolean;
	locked?: boolean;
	/** Text while pending; defaults to the normal label. */
	pendingLabel?: string;
}) {
	// A wrapper (OwnerGate's Slot) may pass its own aria-disabled; keep it.
	const blocked = pending || locked;
	return (
		<Button
			{...props}
			type="submit"
			aria-busy={pending || undefined}
			aria-disabled={blocked || ariaDisabled === true || ariaDisabled === "true" || undefined}
			onClick={(e: MouseEvent<HTMLButtonElement>) => {
				if (blocked) {
					e.preventDefault();
					return;
				}
				onClick?.(e);
			}}
		>
			{pending ? <LoaderCircle className="animate-spin" aria-hidden /> : null}
			{pending && pendingLabel ? pendingLabel : children}
		</Button>
	);
}
