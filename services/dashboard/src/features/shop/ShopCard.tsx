import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { ShopItem } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date";
import { formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { shopStrings as s } from "@/lib/strings/shop";
import { shopQuery, useCancelRenewal } from "./api";
import { ShopDialog } from "./ShopDialog";

/** The order the kinds are shown in; a kind the engine adds later still shows, last. */
const ORDER = ["Your own AI key", "Services", "Credit packs", "Extras"];

export function groupItems(items: ShopItem[]): { label: string; items: ShopItem[] }[] {
	const labels = [...new Set(items.map((i) => i.kind_label))];
	labels.sort((a, b) => {
		const x = ORDER.indexOf(a);
		const y = ORDER.indexOf(b);
		return (x < 0 ? ORDER.length : x) - (y < 0 ? ORDER.length : y);
	});
	return labels.map((label) => ({ label, items: items.filter((i) => i.kind_label === label) }));
}

/** "₹1,999 + GST · ₹2,359 incl. GST", or just the price when GST adds nothing. */
export function priceLine(item: ShopItem): string {
	const price = formatMinor(item.price_minor, item.currency);
	if (item.inclusive_total_minor === item.price_minor) return fill(s.priceFlat, { price });
	return fill(s.price, { price, total: formatMinor(item.inclusive_total_minor, item.currency) });
}

/**
 * Plan & billing → "Add-ons and services" (P5): what the provider sells to businesses, grouped,
 * with GST shown next to every price. Owners buy; members only look. Nothing is shown while the
 * shop is empty, billing is off or the viewer has no workspace.
 */
export function ShopCard({ canBuy }: { canBuy: boolean }) {
	const q = useQuery(shopQuery);
	const [buying, setBuying] = useState<ShopItem | null>(null);
	const [stopping, setStopping] = useState<ShopItem | null>(null);
	const cancel = useCancelRenewal();
	if (q.isPending) {
		return (
			<section aria-label={s.title} className="rounded-lg border border-border bg-card" aria-busy="true">
				<TableSkeleton rows={2} />
			</section>
		);
	}
	const items = q.data?.items ?? [];
	if (q.isError || items.length === 0) return null;

	const stop = async () => {
		if (!stopping?.grant_id) return;
		try {
			await cancel.mutateAsync(stopping.grant_id);
			toast.success(s.cancel.done, {
				description: fill(s.cancel.doneBody, {
					name: stopping.name,
					date: stopping.owned_until ? formatDate(stopping.owned_until) : "",
				}),
			});
		} catch (e) {
			toast.error(messageFor(toApiError(e)).title);
		}
		setStopping(null);
	};

	return (
		<section aria-label={s.title} data-testid="shop-card" className="min-w-0 rounded-lg border border-border bg-card">
			<div className="border-b border-border px-4 py-3.5">
				<h2 className="text-[15px] font-bold">{s.title}</h2>
				<p className="m-0 text-[12.5px] text-muted-foreground">{s.lead}</p>
			</div>
			{groupItems(items).map((group) => (
				<div key={group.label} data-testid="shop-group" className="border-b border-border last:border-b-0">
					<h3 className="m-0 px-4 pt-3 text-[12px] font-bold tracking-wide text-muted-foreground uppercase">
						{group.label}
					</h3>
					<ul className="m-0 list-none p-0">
						{group.items.map((item) => (
							<ShopRow
								key={item.id}
								item={item}
								canBuy={canBuy}
								onBuy={() => setBuying(item)}
								onStop={() => setStopping(item)}
							/>
						))}
					</ul>
				</div>
			))}
			<ShopDialog item={buying} onOpenChange={(o) => !o && setBuying(null)} />
			<ConfirmDialog
				open={stopping !== null}
				onOpenChange={(o) => !o && setStopping(null)}
				title={fill(s.cancel.title, { name: stopping?.name ?? "" })}
				body={fill(s.cancel.body, { date: stopping?.owned_until ? formatDate(stopping.owned_until) : "" })}
				confirmLabel={s.cancel.confirm}
				pending={cancel.isPending}
				onConfirm={() => void stop()}
			/>
		</section>
	);
}

function ShopRow({
	item,
	canBuy,
	onBuy,
	onStop,
}: {
	item: ShopItem;
	canBuy: boolean;
	onBuy: () => void;
	onStop: () => void;
}) {
	const includedByPlan = item.owned && item.grant_id == null;
	const until = item.owned_until ? formatDate(item.owned_until) : null;
	return (
		<li data-testid="shop-item" className="grid gap-1 border-b border-dashed border-border px-4 py-3 last:border-b-0">
			<div className="flex flex-wrap items-baseline justify-between gap-2">
				<span className="text-[14px] font-semibold">{item.name}</span>
				<span data-testid="shop-price" className="font-mono text-[13px] tabular-nums">
					{priceLine(item)}
				</span>
			</div>
			<p className="m-0 text-[13px] text-foreground-2">{item.gives}</p>
			{item.description ? <p className="m-0 text-[12.5px] text-muted-foreground">{item.description}</p> : null}
			<div className="flex flex-wrap items-center justify-between gap-2 pt-1">
				<span className="text-[12.5px] text-muted-foreground">{item.renews ? s.renews : s.oneTime}</span>
				{includedByPlan ? (
					<Badge variant="ok">{s.included}</Badge>
				) : item.owned ? (
					<span className="flex flex-wrap items-center gap-2 text-[13px]">
						<Badge variant="ok">{until ? fill(s.activeUntil, { date: until }) : s.active}</Badge>
						{item.renewal_cancelled ? (
							<small className="text-muted-foreground">{fill(s.renewalCancelled, { date: until ?? "" })}</small>
						) : canBuy && item.renews && item.grant_id ? (
							<Button type="button" size="sm" variant="ghost" onClick={onStop}>
								{s.cancelRenewal}
							</Button>
						) : null}
					</span>
				) : item.buy_with.length === 0 ? null : canBuy ? (
					<Button type="button" size="sm" variant="default" onClick={onBuy}>
						{s.buy}
					</Button>
				) : (
					<small className="text-muted-foreground">{s.ownerOnly}</small>
				)}
			</div>
		</li>
	);
}
