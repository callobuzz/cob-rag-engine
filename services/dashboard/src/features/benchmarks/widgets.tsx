import { ExternalLink, Info } from "lucide-react";
import type { ReactNode } from "react";
import { Segmented } from "@/components/patterns/Segmented";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle, SheetTrigger } from "@/components/ui/sheet";
import { type Currency, setCurrency } from "@/lib/format/currency";
import { benchmarksStrings as t } from "@/lib/strings/benchmarks";
import { cn } from "@/lib/utils";
import { friendlyName, LANGUAGE_FILTERS, type LanguageFilter } from "./api";

/** A figure with a thin bar under it, so a column of percentages reads at a glance. */
export function Meter({
	value,
	text,
	tone = "primary",
}: {
	value: number | null;
	text: string;
	tone?: "primary" | "warn";
}) {
	return (
		<span className="inline-grid justify-items-end gap-1 max-[600px]:justify-items-start">
			<span>{text}</span>
			{value == null ? null : (
				<span aria-hidden className="block h-1 w-12 overflow-hidden rounded-full bg-secondary">
					<span
						className={cn("block h-full rounded-full", tone === "warn" ? "bg-warning" : "bg-primary")}
						style={{ width: `${Math.max(0, Math.min(1, value)) * 100}%` }}
					/>
				</span>
			)}
		</span>
	);
}

/** The friendly model name over its exact id (the id is what you paste into a setting). */
export function ModelName({ id, hosts }: { id: string; hosts?: readonly string[] }) {
	return (
		<span className="grid gap-0.5">
			<span className="font-display text-[14px] font-bold">{friendlyName(id)}</span>
			<code className="font-mono text-[11.5px] break-all text-muted-foreground">
				{id}
				{hosts && hosts.length > 0 ? ` @ ${hosts.join(", ")}` : ""}
			</code>
		</span>
	);
}

export function LanguageToggle({ value, onChange }: { value: LanguageFilter; onChange: (v: LanguageFilter) => void }) {
	return (
		<Segmented
			label={t.toolbar.language}
			value={value}
			onChange={onChange}
			options={LANGUAGE_FILTERS.map((k) => ({ value: k, label: t.toolbar.languages[k] ?? k }))}
		/>
	);
}

export function CurrencyToggle({ value }: { value: Currency }) {
	return (
		<Segmented
			label={t.toolbar.currency}
			value={value}
			onChange={setCurrency}
			options={[
				{ value: "INR" as const, label: "₹" },
				{ value: "USD" as const, label: "$" },
			]}
		/>
	);
}

/** The "Method" drawer: what was tested, the bars, the date, what it cost and where the harness is. */
export function MethodSheet({
	title,
	description,
	children,
	link,
}: {
	title: string;
	description: string;
	children: ReactNode;
	link: string;
}) {
	return (
		<Sheet>
			<SheetTrigger asChild>
				<Button type="button" variant="outline" size="sm">
					<Info aria-hidden />
					{t.method.open}
				</Button>
			</SheetTrigger>
			<SheetContent side="right" className="w-[520px] max-w-[94vw] gap-0 overflow-y-auto p-0">
				<SheetHeader className="border-b border-border p-4">
					<SheetTitle className="pr-8">{title}</SheetTitle>
					<SheetDescription>{description}</SheetDescription>
				</SheetHeader>
				<div className="grid gap-5 p-4 text-[13px] text-foreground-2">
					{children}
					<a
						href={link}
						target="_blank"
						rel="noopener noreferrer"
						className="inline-flex items-center gap-1 font-semibold text-primary hover:underline"
					>
						{t.method.harness}
						<ExternalLink className="size-3.5" aria-hidden />
					</a>
				</div>
			</SheetContent>
		</Sheet>
	);
}

export function MethodSection({ title, children }: { title: string; children: ReactNode }) {
	return (
		<section className="grid gap-1.5">
			<h3 className="m-0 font-mono text-[10.5px] font-medium tracking-[.08em] text-muted-foreground uppercase">
				{title}
			</h3>
			{children}
		</section>
	);
}
