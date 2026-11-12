import type * as React from "react";
import { cn } from "@/lib/utils";

// shadcn input, token fix: mockup .input (38px, --bg-2 fill, blue focus halo).
function Input({ className, type, ...props }: React.ComponentProps<"input">) {
	return (
		<input
			type={type}
			data-slot="input"
			className={cn(
				"h-[38px] w-full min-w-0 rounded-sm border border-input bg-muted px-3 text-sm text-foreground outline-none transition-[border-color,box-shadow] duration-[var(--t)] placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50 read-only:text-foreground-2",
				"focus-visible:border-primary focus-visible:shadow-[0_0_0_3px_var(--primary-soft)] focus-visible:outline-none",
				"aria-invalid:border-destructive",
				className,
			)}
			{...props}
		/>
	);
}

export { Input };
