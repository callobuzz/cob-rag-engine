import { ChevronDown } from "lucide-react";
import type * as React from "react";
import { cn } from "@/lib/utils";

/** Mockup `select.input`: a native <select> styled like Input (keyboard and screen-reader native). */
export function NativeSelect({ className, children, ...props }: React.ComponentProps<"select">) {
	return (
		<div className={cn("relative", className)}>
			<select
				data-slot="native-select"
				className={cn(
					"h-[38px] w-full min-w-0 appearance-none rounded-sm border border-input bg-muted pr-9 pl-3 text-sm text-foreground outline-none transition-[border-color,box-shadow] duration-[var(--t)] disabled:cursor-not-allowed disabled:opacity-60",
					"focus-visible:border-primary focus-visible:shadow-[0_0_0_3px_var(--primary-soft)] focus-visible:outline-none aria-invalid:border-destructive",
				)}
				{...props}
			>
				{children}
			</select>
			<ChevronDown
				className="pointer-events-none absolute top-1/2 right-3 size-4 -translate-y-1/2 text-muted-foreground"
				aria-hidden
			/>
		</div>
	);
}
