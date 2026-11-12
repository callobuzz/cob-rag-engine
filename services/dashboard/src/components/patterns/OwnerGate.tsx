import { Slot } from "radix-ui";
import type { ReactElement } from "react";
import { toast } from "sonner";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { usePrincipal } from "@/lib/auth/store";
import { can, LOCKED_TEXT, type OwnerAction } from "@/lib/permissions";

/**
 * Wraps an owner-only control. For a member session the control stays visible, gets
 * aria-disabled="true", a tooltip "Only owners can …" and, when clicked, a toast with the same
 * text (mockup behaviour) — never silently hidden.
 */
export function OwnerGate({ action, children }: { action: OwnerAction; children: ReactElement }) {
	const principal = usePrincipal();
	if (can(principal, action)) return children;
	const text = LOCKED_TEXT[action];
	return (
		<Tooltip>
			<TooltipTrigger asChild>
				<Slot.Root
					aria-disabled="true"
					data-locked=""
					className="cursor-not-allowed opacity-45"
					onClickCapture={(e) => {
						e.preventDefault();
						e.stopPropagation();
						toast(text);
					}}
				>
					{children}
				</Slot.Root>
			</TooltipTrigger>
			<TooltipContent>{text}</TooltipContent>
		</Tooltip>
	);
}
