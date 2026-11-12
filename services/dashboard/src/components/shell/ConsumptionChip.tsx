import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useEffect } from "react";
import { chipKind, type ProviderStatus, usd } from "@/features/costs/providers"; // BIL1
import { type Consumption, consumptionQuery, type OpenRouterBalance } from "@/features/usage/consumption";
import type { BillingMeter } from "@/lib/api/billing-types"; // BIL6 meter
import { usePrincipal } from "@/lib/auth/store";
import { formatCost } from "@/lib/format/cost";
import { formatMoney, setEngineRate, useCurrency } from "@/lib/format/currency";
import { formatMinor, type Currency as MinorCurrency } from "@/lib/format/money"; // BIL6 meter
import { formatNumber } from "@/lib/format/number";
import { meterStrings } from "@/lib/strings/billing-statements"; // BIL6 meter
import { costStrings as t } from "@/lib/strings/costs";
import { fill } from "@/lib/strings/en";
import { cn } from "@/lib/utils";

const CHIP =
	"inline-flex h-9 shrink-0 items-center gap-2 rounded-sm border border-border bg-secondary px-3 text-[12.5px] font-semibold whitespace-nowrap text-foreground-2 no-underline hover:text-foreground max-[600px]:hidden";

/**
 * Header consumption indicator (all signed-in users), by what the engine says the viewer is:
 * - platform (super admin, operator workspace): "This month ₹X" (operator-paid cost, all customers)
 *   and the OpenRouter balance when the engine's key is one, turning to a warning when low;
 * - customer on the operator's key: "Questions 412 / 1,000" (just the count on an unlimited plan);
 * - customer on their own key: "Your AI spend ₹X".
 * An unpriced request reads "price unknown" (never ₹0.00). Money follows the ₹ / $ setting; a
 * failed fetch shows nothing rather than a broken header.
 */
export function ConsumptionChip() {
	const currency = useCurrency();
	const q = useQuery(consumptionQuery);
	const data = q.data;
	useEffect(() => {
		if (data) setEngineRate(data.usd_inr_rate);
	}, [data]);
	if (!data) return null;

	const amount = formatCost(data.spend_usd, data.spend_known, currency);
	if (data.view === "platform") {
		// The Costs page is the operator view's; in the operator's workspace (tenant view) the
		// same figures are shown without a link to a page that would not open.
		return (
			<div className="flex items-center gap-2" data-testid="consumption">
				<CostsLink className={CHIP} title={t.chip.platformTitle}>
					{fill(t.chip.platform, { amount })}
				</CostsLink>
				{/* BIL1 providers: with more than OpenRouter configured, the worst provider; else as before */}
				{data.providers?.worst &&
				(data.providers.items.length > 1 || data.providers.worst.provider !== "openrouter") ? (
					<ProviderChip worst={data.providers.worst} />
				) : data.openrouter ? (
					<BalanceChip balance={data.openrouter} />
				) : null}
				{/* /BIL1 providers */}
			</div>
		);
	}
	// BIL6 meter: a billed workspace shows its balance or credit; a member who may not see money
	// (and an unbilled workspace) keeps the questions chip below
	if (data.billing?.money_visible) return <MeterChip meter={data.billing} />;
	// /BIL6 meter
	if (data.view === "byok") {
		return (
			<Link to="/usage" className={CHIP} data-testid="consumption" title={t.chip.spendTitle}>
				{fill(t.chip.spend, { amount })}
			</Link>
		);
	}
	return (
		<Link to="/plan" className={CHIP} data-testid="consumption" title={t.chip.questionsTitle}>
			{questionsText(data)}
		</Link>
	);
}

// BIL6 meter
/** Whole units for the header: a balance is rounded down, an amount owed up, so neither flatters. */
export function wholeMinor(minor: number, currency: MinorCurrency, direction: "down" | "up"): string {
	const units = direction === "down" ? Math.floor(minor / 100) : Math.ceil(minor / 100);
	return formatMinor(units * 100, currency).replace(/\.00$/, "");
}

/** "Balance ₹1,240" (prepaid) or "Credit ₹3,000 of ₹10,000 used" (postpaid); amber when low, rose when paused. */
export function meterText(m: BillingMeter): string {
	const s = meterStrings;
	const available = m.available_minor ?? 0;
	if (m.payment_type === "postpaid") {
		const used = wholeMinor(Math.max(0, -available), m.currency, "up");
		const limit = wholeMinor(m.credit_limit_minor ?? 0, m.currency, "down");
		if (m.paused) return s.postpaidPaused;
		return fill(m.alert_level != null ? s.postpaidLow : s.postpaid, { used, limit });
	}
	const amount = wholeMinor(available, m.currency, "down");
	if (m.paused) return fill(s.prepaidPaused, { amount });
	return fill(m.alert_level != null ? s.prepaidLow : s.prepaid, { amount });
}

function MeterChip({ meter }: { meter: BillingMeter }) {
	const low = !meter.paused && meter.alert_level != null;
	return (
		<Link
			to="/plan"
			data-testid="consumption"
			data-billing={meter.payment_type}
			data-low={low || undefined}
			data-paused={meter.paused || undefined}
			title={meter.paused ? meterStrings.pausedTitle : low ? meterStrings.lowTitle : meterStrings.title}
			className={cn(
				CHIP,
				low && "border-warning/60 bg-warning/10 text-foreground",
				meter.paused && "border-destructive/50 bg-destructive/10 text-destructive",
			)}
		>
			{meterText(meter)}
		</Link>
	);
}
// /BIL6 meter

export function questionsText(c: Pick<Consumption, "questions" | "question_limit">): string {
	return c.question_limit == null
		? fill(t.chip.questionsOnly, { used: formatNumber(c.questions) })
		: fill(t.chip.questionsOf, { used: formatNumber(c.questions), limit: formatNumber(c.question_limit) });
}

/** A link to /admin/costs in the operator view; a plain span anywhere else (that route is admin-only). */
function CostsLink({
	className,
	children,
	...rest
}: {
	className: string;
	title: string;
	children: ReactNode;
	"data-testid"?: string;
	"data-low"?: true;
}) {
	const principal = usePrincipal();
	if (principal?.view === "admin") {
		return (
			<Link to="/admin/costs" className={className} {...rest}>
				{children}
			</Link>
		);
	}
	return (
		<span className={className} {...rest}>
			{children}
		</span>
	);
}

// BIL1 providers
function ProviderChip({ worst }: { worst: ProviderStatus }) {
	const kind = chipKind(worst);
	const attention = worst.status === "low" || worst.status === "empty";
	const text = fill(t.providers.chip[kind], { provider: worst.label, amount: usd(worst.remaining_usd_micro) });
	return (
		<CostsLink
			data-testid="provider-chip"
			data-low={attention || undefined}
			title={t.providers.chip.title}
			className={cn(
				CHIP,
				attention && "border-destructive/50 bg-destructive/10 text-destructive",
				kind === "check" && "font-normal text-muted-foreground",
			)}
		>
			{text}
		</CostsLink>
	);
}
// /BIL1 providers

function BalanceChip({ balance }: { balance: OpenRouterBalance }) {
	const currency = useCurrency();
	if (!balance.available || balance.remaining_usd == null) {
		return (
			<CostsLink
				className={cn(CHIP, "font-normal text-muted-foreground")}
				data-testid="openrouter-balance"
				title={t.balance.unavailable}
			>
				{t.chip.balanceUnavailable}
			</CostsLink>
		);
	}
	const text = fill(balance.low ? t.chip.balanceLow : t.chip.balance, {
		amount: formatMoney(balance.remaining_usd, currency),
	});
	return (
		<CostsLink
			data-testid="openrouter-balance"
			data-low={balance.low || undefined}
			title={balance.low ? t.chip.balanceLowTitle : t.chip.balanceTitle}
			className={cn(CHIP, balance.low && "border-destructive/50 bg-destructive/10 text-destructive")}
		>
			{text}
		</CostsLink>
	);
}
