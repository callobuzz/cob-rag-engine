import { Eye, Globe, KeyRound, Pause, Pencil, Play, Trash2, TriangleAlert } from "lucide-react";
import { CopyButton } from "@/components/patterns/CopyButton";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { formatDate } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { widgetStrings as t } from "@/lib/strings/widget";
import { cn } from "@/lib/utils";
import type { WidgetKey } from "./api";
import { SnippetBox } from "./SnippetBox";
import { siteLabel } from "./schemas";

export type CardAction = "edit" | "toggle" | "rotate" | "delete";

const label = (action: string, name: string) => fill(t.card.actionsFor, { action, name });

/** One widget key (mockup card): name, status, pk_, sites, limits, actions, snippet. */
export function WidgetKeyCard({
	widgetKey: k,
	selected,
	busy,
	onSelect,
	onAction,
}: {
	widgetKey: WidgetKey;
	selected: boolean;
	busy?: boolean;
	onSelect: () => void;
	onAction: (action: CardAction) => void;
}) {
	const headingId = `wk-${k.id}-name`;
	const daily = k.daily_message_limit === 0 ? t.card.unlimited : formatNumber(k.daily_message_limit);
	return (
		<article
			aria-labelledby={headingId}
			data-testid="widget-key"
			data-selected={selected}
			className={cn(
				"grid min-w-0 gap-3 rounded-lg border bg-card p-4",
				selected ? "border-primary shadow-[0_0_0_1px_var(--primary)]" : "border-border",
			)}
		>
			<div className="flex flex-wrap items-start justify-between gap-2">
				<div className="min-w-0">
					<h2 id={headingId} className="text-[16px] font-bold break-words">
						{k.name}
					</h2>
					<div className="text-[12.5px] text-muted-foreground">
						{fill(t.card.created, { date: formatDate(k.created_at) })}
					</div>
				</div>
				<Badge variant={k.is_active ? "ok" : "default"}>
					{k.is_active ? <Play aria-hidden /> : <Pause aria-hidden />}
					{k.is_active ? t.card.active : t.card.paused}
				</Badge>
			</div>

			<div className="flex min-w-0 flex-wrap items-center gap-2 rounded-md border border-border bg-muted px-3 py-2">
				<KeyRound className="size-4 shrink-0 text-muted-foreground" aria-hidden />
				<div className="min-w-0 flex-1">
					<div className="text-[11.5px] text-muted-foreground">{t.card.keyLabel}</div>
					<code className="block font-mono text-[13px] break-all" data-testid="public-key">
						{k.public_key}
					</code>
				</div>
				<CopyButton value={k.public_key} ariaLabel={fill(t.card.copyKey, { name: k.name })} />
			</div>

			<div className="grid gap-1.5">
				<ul aria-label={t.card.sitesLabel} className="m-0 flex list-none flex-wrap gap-1.5 p-0">
					{k.allowed_origins.map((o) => (
						<li
							key={o}
							className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-secondary px-2.5 py-0.5 text-[12.5px]"
						>
							{o === "*" ? (
								<TriangleAlert className="size-3.5 shrink-0 text-warning" aria-hidden />
							) : (
								<Globe className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
							)}
							<span className="truncate">{o === "*" ? t.card.anySite : siteLabel(o)}</span>
						</li>
					))}
				</ul>
				<div className="text-[13px] text-foreground-2" data-testid="limits">
					{fill(t.card.limits, { rpm: formatNumber(k.rate_limit_per_minute), daily })}
				</div>
			</div>

			<div className="flex flex-wrap gap-2">
				<Button
					type="button"
					size="sm"
					variant={selected ? "default" : "outline"}
					aria-pressed={selected}
					aria-label={label(t.card.preview, k.name)}
					onClick={onSelect}
				>
					<Eye aria-hidden />
					{t.card.preview}
				</Button>
				<OwnerGate action="widgetKey.manage">
					<Button type="button" size="sm" aria-label={label(t.card.edit, k.name)} onClick={() => onAction("edit")}>
						<Pencil aria-hidden />
						{t.card.edit}
					</Button>
				</OwnerGate>
				<OwnerGate action="widgetKey.manage">
					<Button
						type="button"
						size="sm"
						disabled={busy}
						aria-label={label(k.is_active ? t.card.pause : t.card.resume, k.name)}
						onClick={() => onAction("toggle")}
					>
						{k.is_active ? <Pause aria-hidden /> : <Play aria-hidden />}
						{k.is_active ? t.card.pause : t.card.resume}
					</Button>
				</OwnerGate>
				<OwnerGate action="widgetKey.manage">
					<Button type="button" size="sm" aria-label={label(t.card.rotate, k.name)} onClick={() => onAction("rotate")}>
						<KeyRound aria-hidden />
						{t.card.rotate}
					</Button>
				</OwnerGate>
				<OwnerGate action="widgetKey.manage">
					<Button
						type="button"
						size="sm"
						variant="ghost"
						className="text-destructive"
						aria-label={label(t.card.delete, k.name)}
						onClick={() => onAction("delete")}
					>
						<Trash2 aria-hidden />
						{t.card.delete}
					</Button>
				</OwnerGate>
			</div>

			<SnippetBox name={k.name} snippet={k.embed_snippet} />
		</article>
	);
}
