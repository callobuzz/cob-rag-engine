import { useQuery } from "@tanstack/react-query";
import { History, Lock, Pencil } from "lucide-react";
import { useState } from "react";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { plansQuery } from "@/features/plans/api";
import { sortPlans } from "@/features/plans/limits";
import type { Plan } from "@/lib/api/accounts-types";
import type { PlanPricing } from "@/lib/api/billing-types";
import { formatDateTime } from "@/lib/format/date";
import { billingStrings as s } from "@/lib/strings/billing";
import { fill } from "@/lib/strings/en";
import { shopStrings } from "@/lib/strings/shop"; // P5 plans
import { planPricingQuery, pricingHistoryQuery } from "../api";
import { pricingSummary } from "../fields";
import { PricingDialog } from "./PricingDialog";
import { ScheduledChange } from "./ScheduledChange"; // TV3 notice

/** Billing → Pricing: one card per plan with its price in a sentence, edit and version history. */
export function PricingTab() {
	const plans = useQuery(plansQuery);
	if (plans.isPending) {
		return (
			<div className="grid gap-4 min-[861px]:grid-cols-2" aria-busy="true">
				<CardSkeleton lines={4} />
				<CardSkeleton lines={4} />
			</div>
		);
	}
	if (plans.isError) return <ErrorState error={plans.error} onRetry={() => void plans.refetch()} />;
	return (
		<div className="grid gap-4">
			<p className="m-0 text-foreground-2">{s.pricing.lead}</p>
			<section aria-label={s.pricing.listLabel} className="grid gap-4 min-[861px]:grid-cols-2 min-[1101px]:grid-cols-3">
				{sortPlans(plans.data.items).map((p) => (
					<PricingCard key={p.id} plan={p} />
				))}
			</section>
		</div>
	);
}

function customerCount(n: number): string {
	return n === 1 ? s.pricing.customersOne : fill(s.pricing.customers, { count: String(n) });
}

function PricingCard({ plan }: { plan: Plan }) {
	const q = useQuery(planPricingQuery(plan.id));
	const [editing, setEditing] = useState(false);
	const [history, setHistory] = useState(false);
	const pricing = q.data;
	const locked = plan.is_internal || pricing?.locked === true;

	return (
		<article
			aria-labelledby={`pricing-${plan.id}`}
			data-testid="pricing-card"
			className="flex min-w-0 flex-col rounded-lg border border-border bg-card"
		>
			<div className="grid gap-2 border-b border-border p-4">
				<h2 id={`pricing-${plan.id}`} className="flex flex-wrap items-center gap-2 text-lg font-bold">
					<span className="min-w-0 break-words">{plan.name}</span>
					{plan.is_internal ? (
						<Badge variant="outline">
							<Lock aria-hidden />
							{s.pricing.locked}
						</Badge>
					) : pricing ? (
						<Badge variant={pricing.version == null ? "default" : "info"}>
							{pricing.version == null ? s.pricing.noPricing : fill(s.pricing.version, { n: String(pricing.version) })}
						</Badge>
					) : null}
				</h2>
				{q.isPending ? (
					<div className="h-4 w-3/4 rounded-md bg-secondary" aria-busy="true" />
				) : q.isError ? (
					<p className="m-0 text-[13px] text-destructive">{s.pricing.loadError}</p>
				) : locked || !pricing ? (
					<p className="m-0 text-foreground-2">{s.pricing.lockedBody}</p>
				) : (
					<>
						<p data-testid="pricing-summary" className="m-0 text-[14.5px] font-semibold">
							{pricingSummary(pricing)}
						</p>
						{/* P5 plans: a price from the starter set stays marked until the deployer edits it */}
						{pricing.created_by === "seed:placeholder" ? (
							<Badge
								variant="warn"
								data-testid="placeholder-badge"
								className="w-fit"
								title={shopStrings.admin.placeholderHint}
							>
								{shopStrings.admin.placeholderBadge}
							</Badge>
						) : null}
						{/* /P5 plans */}
						{pricing.version == null ? (
							<p className="m-0 text-[12.5px] text-muted-foreground">{s.pricing.noPricingBody}</p>
						) : null}
						{/* TV3 notice */}
						{pricing.scheduled ? <ScheduledChange plan={plan} scheduled={pricing.scheduled} /> : null}
						{/* /TV3 notice */}
					</>
				)}
			</div>
			<div className="flex flex-wrap items-center justify-between gap-2 px-4 py-3">
				<span className="text-[12.5px] text-muted-foreground">{customerCount(plan.tenant_count)}</span>
				{locked ? null : (
					<div className="flex gap-2">
						<Button size="sm" variant="ghost" onClick={() => setHistory(true)} disabled={!pricing?.version}>
							<History aria-hidden />
							{s.pricing.history}
						</Button>
						<Button size="sm" variant="outline" onClick={() => setEditing(true)} disabled={!pricing}>
							<Pencil aria-hidden />
							{s.pricing.edit}
						</Button>
					</div>
				)}
			</div>
			{pricing && !locked ? (
				<PricingDialog
					open={editing}
					onOpenChange={setEditing}
					planId={plan.id}
					planName={plan.name}
					current={pricing}
				/>
			) : null}
			{history ? <HistorySheet plan={plan} onClose={() => setHistory(false)} /> : null}
		</article>
	);
}

function HistorySheet({ plan, onClose }: { plan: Plan; onClose: () => void }) {
	const q = useQuery(pricingHistoryQuery(plan.id));
	return (
		<Sheet open onOpenChange={(o) => (o ? undefined : onClose())}>
			<SheetContent className="overflow-y-auto sm:max-w-[460px]">
				<SheetHeader>
					<SheetTitle>{fill(s.history.title, { name: plan.name })}</SheetTitle>
					<SheetDescription>{s.history.lead}</SheetDescription>
				</SheetHeader>
				<div className="grid gap-3 px-4 pb-4">
					{q.isPending ? (
						<CardSkeleton lines={3} />
					) : q.isError ? (
						<ErrorState error={q.error} onRetry={() => void q.refetch()} />
					) : q.data.items.length === 0 ? (
						<p className="m-0 text-foreground-2">{s.history.empty}</p>
					) : (
						<ol className="m-0 grid list-none gap-3 p-0" data-testid="pricing-history">
							{q.data.items.map((v: PlanPricing) => (
								<li key={v.version} className="grid gap-1 rounded-lg border border-border p-3">
									<div className="flex flex-wrap items-center gap-2 text-[13.5px] font-bold">
										{fill(s.pricing.version, { n: String(v.version) })}
										{v.is_current ? <Badge variant="ok">{s.history.current}</Badge> : null}
									</div>
									<p className="m-0 text-[13px]">{pricingSummary(v)}</p>
									<small className="text-[12.5px] text-muted-foreground">
										{v.created_at ? formatDateTime(v.created_at) : null}
										{v.created_by ? ` · ${fill(s.history.by, { who: v.created_by })}` : null}
									</small>
								</li>
							))}
						</ol>
					)}
				</div>
			</SheetContent>
		</Sheet>
	);
}
