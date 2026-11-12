import { cva, type VariantProps } from "class-variance-authority";
import { Slot } from "radix-ui";
import type * as React from "react";
import { cn } from "@/lib/utils";

// shadcn button, token fix: variants restyled to the mockup's .btn family.
const buttonVariants = cva(
	"inline-flex shrink-0 items-center justify-center gap-2 rounded-sm border text-[13.5px] font-semibold whitespace-nowrap transition-[background,border-color,filter] duration-[var(--t)] outline-none disabled:pointer-events-none disabled:opacity-45 aria-disabled:cursor-not-allowed aria-disabled:opacity-45 [&_svg]:pointer-events-none [&_svg]:shrink-0 [&_svg:not([class*='size-'])]:size-4",
	{
		variants: {
			variant: {
				default: "border-transparent bg-primary text-primary-foreground hover:brightness-110",
				brand: "border-transparent bg-brand text-brand-foreground hover:brightness-110",
				destructive:
					"border-[color-mix(in_srgb,var(--destructive)_45%,transparent)] bg-transparent text-destructive hover:bg-destructive-soft",
				outline: "border-input bg-card text-foreground hover:bg-accent",
				secondary: "border-border bg-secondary text-foreground hover:bg-accent",
				ghost: "border-transparent bg-transparent text-foreground hover:bg-accent",
				link: "h-auto! border-0 p-0! text-brand-text underline-offset-4 hover:underline",
			},
			size: {
				default: "h-9 px-3.5",
				sm: "h-[30px] px-2.5 text-[12.5px]",
				lg: "h-[42px] px-4",
				icon: "size-9",
				"icon-sm": "size-8",
			},
		},
		defaultVariants: {
			variant: "outline",
			size: "default",
		},
	},
);

function Button({
	className,
	variant,
	size,
	asChild = false,
	...props
}: React.ComponentProps<"button"> &
	VariantProps<typeof buttonVariants> & {
		asChild?: boolean;
	}) {
	const Comp = asChild ? Slot.Root : "button";

	return (
		<Comp
			data-slot="button"
			data-variant={variant ?? "outline"}
			className={cn(buttonVariants({ variant, size, className }))}
			{...props}
		/>
	);
}

export { Button, buttonVariants };
