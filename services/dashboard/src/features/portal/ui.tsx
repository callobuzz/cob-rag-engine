import { useQuery } from "@tanstack/react-query";
import { Ban, CircleCheck, CircleDashed, CircleX, Clock, Layers, Loader, PowerOff } from "lucide-react";
import type { ReactNode } from "react";
import { z } from "zod";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { infoQuery } from "@/lib/api/queries";
import { portalStrings as t } from "@/lib/strings/portal";
import { cn } from "@/lib/utils";

export const HOME_TABS = ["workspaces", "rules"] as const;
export type HomeTab = (typeof HOME_TABS)[number];
export const PANELS = ["staging", "chunks", "chat", "audit"] as const;
export type Panel = (typeof PANELS)[number];

/** `/portal?kb=&status=&page=&tab=` (D11). */
export const portalSearchSchema = z.object({
	kb: z.uuid().optional().catch(undefined),
	status: z.enum(["draft", "in_review", "approved", "ingested", "archived"]).optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
	tab: z.enum(HOME_TABS).optional().catch(undefined),
});

export type PortalSearch = z.infer<typeof portalSearchSchema>;

/** `/portal/$workspaceId?kb=&panel=&status=&page=` (D11; status and page filter the chunks). */
export const workspaceSearchSchema = z.object({
	kb: z.uuid().optional().catch(undefined),
	panel: z.enum(PANELS).optional().catch(undefined),
	status: z.enum(["draft", "approved", "rejected"]).optional().catch(undefined),
	page: z.coerce.number().int().min(1).optional().catch(undefined),
});

export type WorkspaceSearch = z.infer<typeof workspaceSearchSchema>;

/**
 * The portal exists only when `GET /info` reports `features.content_portal`; otherwise the page
 * says it is switched off (the nav item is hidden by the shell).
 */
export function PortalGate({ children }: { children: ReactNode }) {
	const info = useQuery(infoQuery);
	if (info.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				<CardSkeleton lines={1} />
				<CardSkeleton lines={4} />
			</div>
		);
	}
	if (!info.data?.features.content_portal) {
		return (
			<>
				<PageHeader title={t.off.title} />
				<section
					data-testid="portal-off"
					className="grid justify-items-start gap-3.5 rounded-lg border border-border bg-card p-8"
				>
					<span className="grid size-[52px] place-items-center rounded-[14px] bg-secondary text-muted-foreground">
						<PowerOff className="size-[26px]" aria-hidden />
					</span>
					<h2 className="text-xl font-bold">{t.off.heading}</h2>
					<p className="m-0 max-w-[620px] text-foreground-2">{t.off.body}</p>
				</section>
			</>
		);
	}
	return <>{children}</>;
}

const WS_STATUS: Record<string, { variant: "ok" | "warn" | "bad" | "info" | "default"; icon: typeof Clock }> = {
	draft: { variant: "default", icon: CircleDashed },
	in_review: { variant: "warn", icon: Clock },
	approved: { variant: "info", icon: CircleCheck },
	ingested: { variant: "ok", icon: CircleCheck },
	archived: { variant: "default", icon: Ban },
};

/** Workspace status as icon + text. */
export function WorkspaceStatusBadge({ status }: { status: string }) {
	const m = WS_STATUS[status] ?? { variant: "default" as const, icon: CircleDashed };
	const Icon = m.icon;
	return (
		<Badge variant={m.variant} data-testid="workspace-status">
			<Icon aria-hidden />
			{t.status[status] ?? status}
		</Badge>
	);
}

const CHUNK_STATUS: Record<string, { variant: "ok" | "bad" | "default"; icon: typeof Clock }> = {
	draft: { variant: "default", icon: CircleDashed },
	approved: { variant: "ok", icon: CircleCheck },
	rejected: { variant: "bad", icon: CircleX },
};

export function ChunkStatusBadge({ status }: { status: string }) {
	const m = CHUNK_STATUS[status] ?? { variant: "default" as const, icon: CircleDashed };
	const Icon = m.icon;
	return (
		<Badge variant={m.variant}>
			<Icon aria-hidden />
			{t.chunks.status[status] ?? status}
		</Badge>
	);
}

const AI_STATUS: Record<string, { variant: "ok" | "bad" | "info" | "default"; icon: typeof Clock }> = {
	pending: { variant: "default", icon: Clock },
	processing: { variant: "info", icon: Loader },
	completed: { variant: "ok", icon: CircleCheck },
	failed: { variant: "bad", icon: CircleX },
};

export function AiStatusBadge({ status }: { status: string }) {
	const m = AI_STATUS[status] ?? { variant: "default" as const, icon: CircleDashed };
	const Icon = m.icon;
	return (
		<Badge variant={m.variant}>
			<Icon aria-hidden />
			{t.staging.ai[status] ?? status}
		</Badge>
	);
}

export function KindBadge({ kind }: { kind: string }) {
	return (
		<Badge variant={kind === "post_ingest_review" ? "info" : "default"}>
			<Layers aria-hidden />
			{t.kind[kind] ?? kind}
		</Badge>
	);
}

/** Underlined tabs (same look as the document detail tabs). */
export function LineTabs<T extends string>({
	label,
	value,
	options,
	onChange,
	children,
}: {
	label: string;
	value: T;
	options: { value: T; label: ReactNode }[];
	onChange: (value: T) => void;
	children: ReactNode;
}) {
	return (
		<Tabs value={value} onValueChange={(v) => onChange(v as T)} className="min-w-0 gap-4">
			<TabsList
				variant="line"
				aria-label={label}
				className="h-auto! w-full justify-start gap-1 overflow-x-auto overflow-y-hidden rounded-none border-b border-border p-0"
			>
				{options.map((o) => (
					<TabsTrigger
						key={o.value}
						value={o.value}
						className={cn(
							"h-10! flex-none rounded-none px-3 font-semibold text-foreground-2 hover:text-foreground data-[state=active]:text-foreground",
							"after:bottom-[-1px]! after:inset-x-2! after:h-0.5! after:rounded-sm after:bg-primary!",
						)}
					>
						{o.label}
					</TabsTrigger>
				))}
			</TabsList>
			{children}
		</Tabs>
	);
}
