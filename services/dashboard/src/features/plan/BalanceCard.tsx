import { CirclePause, TriangleAlert } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { OwnBillingView } from "@/lib/api/billing-types";
import { formatDate } from "@/lib/format/date";
import { formatMinor } from "@/lib/format/money";
import { formatNumber } from "@/lib/format/number";
import { planBillingStrings } from "@/lib/strings/billing-statements";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";

const s = planBillingStrings.balance;

/** "About 620 answers left at ₹2.00 each", for a plan that charges a flat price per answer. */
export function answersLeft(view: OwnBillingView): string | null {
	const price = view.per_question_minor ?? 0;
	const left = view.available_minor ?? 0;
	if (view.usage_mode !== "per_question" || price <= 0 || left <= 0) return null;
	const n = Math.floor(left / price);
	const money = formatMinor(price, view.currency);
	return n === 1 ? fill(s.answersLeftOne, { price: money }) : fill(s.answersLeft, { n: formatNumber(n), price: money });
}

/** The credit used so far (postpaid): what is owed, never below zero. */
export function creditUsed(view: OwnBillingView): number {
	return Math.max(0, -(view.available_minor ?? 0));
}

function Notice({ tone, children }: { tone: "warn" | "bad"; children: React.ReactNode }) {
	const Icon = tone === "bad" ? CirclePause : TriangleAlert;
	return (
		<p
			role="status"
			data-testid={tone === "bad" ? "balance-paused" : "balance-low"}
			className={cn(
				"m-0 flex items-start gap-2 rounded-md border px-3 py-2 text-[13px]",
				tone === "bad"
					? "border-destructive/40 bg-destructive/10 text-destructive"
					: "border-warning/40 bg-warning/10 text-foreground",
			)}
		>
			<Icon aria-hidden className="mt-0.5 size-4 shrink-0" />
			<span>{children}</span>
		</p>
	);
}

/**
 * The balance of a prepaid workspace, or the credit used of a postpaid one (BIL6): the amount that
 * matters first, what it buys, a low or paused notice, the next charge, and "Request top-up"
 * (owners only). Shown only where the plan charges something and the viewer may see money.
 */
export function BalanceCard({
	view,
	canRequest,
	onRequestTopUp,
}: {
	view: OwnBillingView;
	canRequest: boolean;
	onRequestTopUp: () => void;
}) {
	const postpaid = view.payment_type === "postpaid";
	const paused = view.paused === true;
	const low = !paused && view.alert_level != null;
	const used = creditUsed(view);
	const limit = view.credit_limit_minor ?? 0;
	const share = limit > 0 ? Math.min(1, used / limit) : 0;
	const creditText = fill(s.creditOf, {
		used: formatMinor(used, view.currency),
		limit: formatMinor(limit, view.currency),
	});
	const meterProps = {
		role: "meter" as const,
		"aria-label": s.creditMeter,
		"aria-valuemin": 0,
		"aria-valuemax": limit,
		"aria-valuenow": Math.min(used, limit),
		"aria-valuetext": creditText,
	};
	const hint = postpaid
		? null
		: view.usage_mode === "per_question"
			? answersLeft(view)
			: view.usage_mode === "cost_plus"
				? s.costPlus
				: null;
	return (
		<section
			aria-label={postpaid ? s.credit : s.title}
			data-testid="balance-card"
			data-payment={view.payment_type}
			className="min-w-0 rounded-lg border border-border bg-card"
		>
			<div className="grid gap-3 px-4 py-4">
				<div className="flex flex-wrap items-start justify-between gap-3">
					<div className="min-w-0">
						<div className="text-[13px] text-muted-foreground">{postpaid ? s.credit : s.prepaid}</div>
						{postpaid ? (
							<div className="mt-0.5 font-mono text-[24px] font-bold tabular-nums" data-testid="balance-amount">
								{creditText}
							</div>
						) : (
							<div className="mt-0.5 font-mono text-[28px] font-bold tabular-nums" data-testid="balance-amount">
								{formatMinor(view.available_minor ?? 0, view.currency)}
							</div>
						)}
					</div>
					{paused ? <Badge variant="bad">Paused</Badge> : low ? <Badge variant="warn">Low</Badge> : null}
				</div>
				{postpaid && limit > 0 ? (
					<div {...meterProps} className="h-1.5 overflow-hidden rounded-[3px] bg-secondary">
						<i
							className={cn(
								"block h-full rounded-[3px]",
								paused ? "bg-destructive" : low ? "bg-warning" : "bg-primary",
							)}
							style={{ width: `${Math.round(share * 1000) / 10}%` }}
						/>
					</div>
				) : null}
				{hint ? (
					<p className="m-0 text-[13px] text-foreground-2" data-testid="balance-hint">
						{hint}
					</p>
				) : null}
				{postpaid && !paused && !low ? <p className="m-0 text-[13px] text-foreground-2">{s.creditHint}</p> : null}
				{paused ? (
					<Notice tone="bad">{view.pause_reason === "forced" ? s.forced : postpaid ? s.creditPaused : s.paused}</Notice>
				) : low ? (
					<Notice tone="warn">{postpaid ? s.creditLow : s.low}</Notice>
				) : null}
				{view.trial_ends_at ? (
					<p className="m-0 text-[12.5px] text-muted-foreground">
						{fill(s.trial, { date: formatDate(view.trial_ends_at) })}
					</p>
				) : view.next_charge_on ? (
					<p className="m-0 text-[12.5px] text-muted-foreground">
						{fill(s.nextCharge, { date: formatDate(view.next_charge_on) })}
					</p>
				) : null}
			</div>
			{canRequest && !postpaid ? (
				<div className="flex flex-wrap items-center justify-between gap-3 rounded-b-lg border-t border-border bg-secondary px-4 py-3">
					<small className="text-[12.5px] text-muted-foreground">{s.requestHint}</small>
					<Button type="button" size="sm" variant="default" onClick={onRequestTopUp}>
						{s.requestTopUp}
					</Button>
				</div>
			) : null}
		</section>
	);
}
