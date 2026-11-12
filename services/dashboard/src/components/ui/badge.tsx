import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type * as React from "react";
import { cn } from "@/lib/utils";

// shadcn badge, token fix: mockup .badge with ok / warn / bad / info tones.
const badgeVariants = cva(
	"inline-flex h-[22px] w-fit shrink-0 items-center justify-center gap-1.5 overflow-hidden rounded-full border px-2 text-[11.5px] font-semibold whitespace-nowrap [&>svg]:pointer-events-none [&>svg]:size-3",
	{
		variants: {
			variant: {
				default: "border-border bg-secondary text-foreground-2",
				ok: "border-transparent bg-success-soft text-success",
				warn: "border-transparent bg-warning-soft text-warning",
				bad: "border-transparent bg-destructive-soft text-destructive",
				info: "border-transparent bg-primary-soft text-primary",
				outline: "border-border text-foreground",
			},
		},
		defaultVariants: {
			variant: "default",
		},
	},
);

function Badge({
	className,
	variant,
	asChild = false,
	...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants> & { asChild?: boolean }) {
	const Comp = asChild ? Slot.Root : "span";

	return (
		<Comp
			data-slot="badge"
			data-variant={variant ?? "default"}
			className={cn(badgeVariants({ variant }), className)}
			{...props}
		/>
	);
}

export { Badge, badgeVariants };
