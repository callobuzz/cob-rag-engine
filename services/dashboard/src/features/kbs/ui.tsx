import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

/** Mockup `.card` with `.card-head` (title, sub, right-side slot). */
export function Panel({
	title,
	sub,
	aside,
	children,
	className,
	tone,
}: {
	title: string;
	sub?: ReactNode;
	aside?: ReactNode;
	children: ReactNode;
	className?: string;
	tone?: "danger";
}) {
	return (
		<section
			aria-label={title}
			className={cn(
				"min-w-0 rounded-lg border bg-card",
				tone === "danger" ? "border-[color-mix(in_srgb,var(--destructive)_40%,var(--border))]" : "border-border",
				className,
			)}
		>
			<div className="flex flex-wrap items-center justify-between gap-3 border-b border-border px-4 py-3.5">
				<div className="min-w-0">
					<h2 className="text-[15px] font-bold">{title}</h2>
					{sub ? <div className="text-[12.5px] text-muted-foreground">{sub}</div> : null}
				</div>
				{aside}
			</div>
			{children}
		</section>
	);
}

/** Mockup `.form-foot`: right-aligned actions on the panel's tinted footer. */
export function FormFoot({ children }: { children: ReactNode }) {
	return (
		<div className="flex flex-wrap justify-end gap-2 rounded-b-lg border-t border-border bg-secondary px-4 py-3">
			{children}
		</div>
	);
}

/** Mockup `.settings`: main column + side column, one column under 1101 px. */
export function SettingsGrid({ children }: { children: ReactNode }) {
	return (
		<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.5fr)_minmax(0,1fr)]">{children}</div>
	);
}

/** Mockup `.form-2`: two fields side by side from 861 px. */
export function Form2({ children }: { children: ReactNode }) {
	return <div className="grid gap-4 min-[861px]:grid-cols-2">{children}</div>;
}

/** Mockup `.kpi` card. */
export function Kpi({ label, value, delta }: { label: string; value: ReactNode; delta?: ReactNode }) {
	return (
		<div className="grid gap-1 rounded-lg border border-border bg-card p-4">
			<div className="text-[13px] text-foreground-2">{label}</div>
			<div className="font-display text-[28px] font-bold tracking-[-.02em] tabular-nums max-[520px]:text-[22px]">
				{value}
			</div>
			{delta ? <div className="text-[12.5px] text-muted-foreground">{delta}</div> : null}
		</div>
	);
}
