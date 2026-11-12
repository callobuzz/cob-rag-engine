import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Badge } from "@/components/ui/badge";
import { ownOrdersQuery } from "@/features/shop/api";
import type { ServiceOrderStatus } from "@/lib/api/billing-types";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatDate } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { shopStrings as s } from "@/lib/strings/shop";

const TONE: Record<ServiceOrderStatus, "default" | "info" | "ok" | "warn"> = {
	requested: "warn",
	in_progress: "info",
	done: "ok",
	cancelled: "default",
};

/**
 * Plan & billing → "Your orders" (P5): the services this workspace bought, with the provider's note
 * and the date support is included until. Members may read it (a status is not money). Hidden while
 * there are no orders, billing is off or the viewer has no workspace.
 */
export function OwnerServiceOrders() {
	const { productName } = useBranding();
	const q = useQuery(ownOrdersQuery);
	const items = q.data?.items ?? [];
	if (q.isError || items.length === 0) return null;
	return (
		<section
			aria-label={s.orders.title}
			data-testid="owner-orders"
			className="min-w-0 rounded-lg border border-border bg-card"
		>
			<div className="border-b border-border px-4 py-3.5">
				<h2 className="text-[15px] font-bold">{s.orders.title}</h2>
				<p className="m-0 text-[12.5px] text-muted-foreground">{s.orders.lead}</p>
			</div>
			<ul className="m-0 list-none p-0">
				{items.map((o) => (
					<li
						key={o.id}
						data-testid="owner-order"
						className="grid gap-1 border-b border-dashed border-border px-4 py-3 last:border-b-0"
					>
						<div className="flex flex-wrap items-center justify-between gap-2">
							<span className="text-[14px] font-semibold">{o.name}</span>
							<Badge data-testid="order-status" variant={TONE[o.status]}>
								{s.orders.status[o.status]}
							</Badge>
						</div>
						<small className="text-muted-foreground">
							{fill(s.orders.ordered, { date: formatDate(o.created_at) })}
						</small>
						{o.owner_note ? (
							<p className="m-0 text-[13px] text-foreground-2">
								<b>{s.orders.yourNote}:</b> {o.owner_note}
							</p>
						) : null}
						{o.public_note ? (
							<p data-testid="order-note" className="m-0 border-l-2 border-border pl-3 text-[13px]">
								<b>{fill(s.orders.fromProduct, { product: productName })}:</b> {o.public_note}
							</p>
						) : null}
						{o.support_until ? (
							<small data-testid="order-support" className="text-muted-foreground">
								{fill(s.orders.supportUntil, { date: formatDate(o.support_until) })}
							</small>
						) : null}
						{o.support_request_id ? (
							<Link
								to="/support"
								search={{ request: o.support_request_id }}
								className="w-fit text-[12.5px] font-medium text-primary hover:underline"
							>
								{s.orders.ask}
							</Link>
						) : null}
					</li>
				))}
			</ul>
		</section>
	);
}
