import { CircleAlert, Info, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Mockup `.alert` (bad / warn / info). `bad` is announced with role="alert". */
export function FormAlert({
	tone = "bad",
	children,
	id,
	className,
}: {
	tone?: "bad" | "warn" | "info";
	children: ReactNode;
	id?: string;
	className?: string;
}) {
	const Icon = tone === "bad" ? CircleAlert : tone === "warn" ? TriangleAlert : Info;
	return (
		<div
			id={id}
			role={tone === "bad" ? "alert" : undefined}
			className={cn(
				"flex gap-2.5 rounded-sm px-3 py-2.5 text-[13px] leading-[1.45]",
				tone === "bad" && "bg-destructive-soft text-destructive",
				tone === "warn" && "bg-warning-soft text-warning",
				tone === "info" && "bg-primary-soft text-foreground-2",
				className,
			)}
		>
			<Icon className="mt-px size-[18px] shrink-0" aria-hidden />
			<div className="min-w-0">{children}</div>
		</div>
	);
}
