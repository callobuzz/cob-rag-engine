import { CircleCheckIcon, InfoIcon, Loader2Icon, OctagonXIcon, TriangleAlertIcon } from "lucide-react";
import type * as React from "react";
import { Toaster as Sonner, type ToasterProps } from "sonner";
import { useResolvedTheme } from "@/lib/theme";

// shadcn sonner, adapted: theme from src/lib/theme.ts instead of next-themes.
const Toaster = ({ ...props }: ToasterProps) => {
	const theme = useResolvedTheme();

	return (
		<Sonner
			theme={theme}
			position="bottom-right"
			className="toaster group"
			icons={{
				success: <CircleCheckIcon className="size-4 text-success" />,
				info: <InfoIcon className="size-4" />,
				warning: <TriangleAlertIcon className="size-4 text-warning" />,
				error: <OctagonXIcon className="size-4 text-destructive" />,
				loading: <Loader2Icon className="size-4 animate-spin" />,
			}}
			style={
				{
					"--normal-bg": "var(--popover)",
					"--normal-text": "var(--popover-foreground)",
					"--normal-border": "var(--input)",
					"--border-radius": "var(--radius)",
				} as React.CSSProperties
			}
			{...props}
		/>
	);
};

export { Toaster };
