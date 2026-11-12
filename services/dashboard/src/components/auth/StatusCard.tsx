import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

const TONES = {
	wait: "bg-warning-soft text-warning",
	stop: "bg-destructive-soft text-destructive",
	talk: "bg-brand-soft text-brand-text",
	mail: "bg-primary-soft text-primary",
} as const;

/** Mockup `.auth-status`: icon tile, heading, body, then actions. */
export function StatusCard({
	icon: Icon,
	tone,
	title,
	children,
}: {
	icon: LucideIcon;
	tone: keyof typeof TONES;
	title: string;
	children?: ReactNode;
}) {
	return (
		<div className="grid justify-items-start gap-3.5">
			<span className={cn("grid size-[52px] place-items-center rounded-[14px]", TONES[tone])}>
				<Icon className="size-[26px]" aria-hidden />
			</span>
			<h1 className="text-2xl font-bold" tabIndex={-1}>
				{title}
			</h1>
			{children}
		</div>
	);
}
