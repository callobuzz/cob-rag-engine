import { useQuery } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { corporaSwitcherQuery } from "@/lib/api/queries";
import { useRagUrl } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { cn } from "@/lib/utils";

/** Mockup `.tabs` under a page header, `?tab=` in the URL (same look as the KB detail tabs). */
export function PageTabs<T extends string>({
	label,
	tabs,
	value,
	labels,
	onChange,
	panels,
}: {
	label: string;
	tabs: readonly T[];
	value: T;
	labels: Record<T, string>;
	onChange: (tab: T) => void;
	panels: Record<T, () => ReactNode>;
}) {
	return (
		<Tabs value={value} onValueChange={(v) => onChange(v as T)} className="min-w-0 gap-4">
			<TabsList
				variant="line"
				aria-label={label}
				className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0 [scrollbar-width:none]"
			>
				{tabs.map((k) => (
					<TabsTrigger
						key={k}
						value={k}
						className={cn(
							"h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
							"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
						)}
					>
						{labels[k]}
					</TabsTrigger>
				))}
			</TabsList>
			{tabs.map((k) => (
				<TabsContent key={k} value={k} className="grid min-w-0 gap-4">
					{value === k ? panels[k]() : null}
				</TabsContent>
			))}
		</Tabs>
	);
}

/** Knowledge-base names by id, from the switcher's list (already cached by the shell). */
export function useKbNames(): (id: string | null | undefined) => string {
	const q = useQuery(corporaSwitcherQuery);
	const items = q.data?.items ?? [];
	return (id) => (id ? (items.find((k) => k.id === id)?.name ?? "—") : "—");
}

/** A labelled filter row above a list or chart (dataviz: filters in one row above). */
export function FilterRow({ children, className }: { children: ReactNode; className?: string }) {
	return <div className={cn("flex flex-wrap items-end gap-3 px-4 py-3", className)}>{children}</div>;
}

/** Plain-text note in a card (tenant notes, "one knowledge base at a time"). */
export function Note({ children, testId }: { children: ReactNode; testId?: string }) {
	return (
		<p data-testid={testId} className="m-0 px-4 py-3 text-[13px] text-muted-foreground">
			{children}
		</p>
	);
}

/**
 * Error text for a toast or an inline alert: the error map, except that a 503 with a plain
 * `detail` shows it (gap detection "No embedder available", "Task broker unavailable").
 */
export function useErrorText(): (error: unknown) => string {
	const ragUrl = useRagUrl();
	const { supportEmail } = useBranding();
	return (error) => {
		const e = toApiError(error);
		if (e.status === 503 && e.detail) return e.detail;
		return messageFor(e, { ragUrl, support: supportEmail }).title;
	};
}
