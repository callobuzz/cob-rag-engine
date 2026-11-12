import { AlertTriangle } from "lucide-react";
import { Kpi } from "@/features/kbs/ui";
import type { OpenRouterBalance } from "@/features/usage/consumption";
import { formatMoney, useCurrency } from "@/lib/format/currency";
import { costStrings as t } from "@/lib/strings/costs";
import { fill } from "@/lib/strings/en";

/** The engine key's OpenRouter balance (only when that is the engine's provider), low-balance warning included. */
export function BalanceCard({ balance }: { balance: OpenRouterBalance }) {
	const currency = useCurrency();
	const money = (usd: number | null | undefined) => (usd == null ? "—" : formatMoney(usd, currency, { exact: true }));
	if (!balance.available) {
		return <Kpi label={t.balance.title} value="—" delta={t.balance.unavailable} />;
	}
	if (balance.remaining_usd == null) {
		return (
			<Kpi
				label={t.balance.title}
				value="—"
				delta={`${t.balance.noBalance} ${t.balance.spent}: ${money(balance.usage_usd)}.`}
			/>
		);
	}
	return (
		<div data-testid="balance-card" data-low={balance.low || undefined}>
			<Kpi
				label={t.balance.title}
				value={money(balance.remaining_usd)}
				delta={
					balance.low ? (
						<span role="alert" className="inline-flex items-center gap-1 font-semibold text-destructive">
							<AlertTriangle className="size-3.5" aria-hidden />
							{fill(t.balance.low, { threshold: money(balance.low_threshold_usd) })}
						</span>
					) : (
						`${t.balance.spent}: ${money(balance.usage_usd)}${
							balance.usage_monthly_usd == null ? "" : ` · ${t.balance.thisMonth}: ${money(balance.usage_monthly_usd)}`
						}`
					)
				}
			/>
		</div>
	);
}
