import { useQuery } from "@tanstack/react-query";
import { useState } from "react"; // BIL6
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { UsageMeter, usageShare } from "@/components/patterns/UsageMeter";
import { PageHeader } from "@/components/shell/PageHeader";
import { ActiveAddOns, ownBillingQuery } from "@/features/billing/addons/ActiveAddOns"; // BIL5 add-ons
import { OwnerFairUseCard } from "@/features/fair-use/OwnerFairUseCard"; // PIP4
import { InvoicesCard } from "@/features/invoices/InvoicesCard"; // P2 invoices
import { PayCard } from "@/features/payments/PayCard"; // P1 payments
import { PaymentsCard } from "@/features/payments/PaymentsCard"; // P1 payments
import { ChangePlanCard } from "@/features/plan-choice/ChangePlanCard"; // P6 plan choice
import { formatSizeLimit, SIZE_KEYS } from "@/features/plans/limits";
import { OwnerServiceOrders } from "@/features/service-orders/OwnerServiceOrders"; // P5 plans
import { ShopCard } from "@/features/shop/ShopCard"; // P5 plans
import { useAllCorpora } from "@/features/usage/api";
import type { PlanUsageResponse } from "@/lib/api/accounts-types";
import { usePrincipal } from "@/lib/auth/store";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatDate } from "@/lib/format/date";
import { formatPercent } from "@/lib/format/number";
import { plansStrings } from "@/lib/strings/accounts";
import { planBillingStrings as billing } from "@/lib/strings/billing-statements"; // BIL6
import { fill } from "@/lib/strings/en";
import { planPageStrings as t } from "@/lib/strings/overview";
import { planMeQuery, tenantMeQuery } from "./api";
import { BalanceCard } from "./BalanceCard"; // BIL6
import { MonthByKb } from "./MonthByKb";
import { NeedMoreCard } from "./NeedMoreCard";
import { PriceChangeBanner } from "./PriceChangeBanner"; // TV3
import { RenewalCard } from "./RenewalCard"; // TV3
import { RequestTopUpDialog } from "./RequestTopUpDialog"; // BIL6
import { SpendCapCard } from "./SpendCapCard"; // TV3
import { StatementsCard } from "./StatementsCard"; // BIL6
import { UsageThisPeriod } from "./UsageThisPeriod"; // BIL6

/** Hint under "Questions this month": share used, reset date (local), what counts. */
export function questionsHint(p: PlanUsageResponse): string {
	const share = usageShare(p.usage.questions, p.limits.monthly_questions);
	const date = formatDate(p.resets_at);
	return share == null
		? fill(t.questionsHintUnlimited, { date })
		: fill(t.questionsHint, { pct: formatPercent(share), date });
}

/**
 * `/plan` (tenant; mockup `[data-view=plan]`): the plan, all four limits against this month's use
 * (`PlanUsageResponse`), how to get more, and this month by knowledge base. Members see it too (A5).
 */
export function PlanPage() {
	const principal = usePrincipal();
	const { productName } = useBranding();
	const plan = useQuery(planMeQuery);
	const me = useQuery(tenantMeQuery);
	const corpora = useAllCorpora();
	// BIL6: the workspace's money, when its plan charges anything and the viewer may see it
	const own = useQuery(ownBillingQuery);
	const [topUpOpen, setTopUpOpen] = useState(false);
	const isMember = principal?.auth === "session" && principal.role === "member";
	const view = own.data;
	const billed = view?.billed === true;
	const showMoney = billed && view?.money_visible === true;
	// /BIL6
	const tenant = me.data?.name ?? principal?.tenant?.name ?? "";
	const p = plan.data;
	const kbNames = (corpora.data?.items ?? []).map((c) => c.name);
	return (
		<div className="grid gap-4">
			<PageHeader title={t.title} description={fill(t.lead, { tenant })} />
			{/* TV3 */}
			{view && showMoney ? <PriceChangeBanner view={view} /> : null}
			{/* /TV3 */}
			<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1.65fr)_minmax(0,1fr)]">
				<div className="grid min-w-0 content-start gap-4">
					<section aria-label={t.current} className="min-w-0 rounded-lg border border-border bg-card">
						{plan.isPending ? (
							<TableSkeleton rows={5} />
						) : plan.isError ? (
							<div className="p-4">
								<ErrorState error={plan.error} title={t.error} onRetry={() => void plan.refetch()} />
							</div>
						) : (
							<>
								<div className="flex flex-wrap items-start justify-between gap-4 border-b border-border px-4 py-[18px]">
									<div className="min-w-0">
										<div className="text-[13px] text-muted-foreground">{t.current}</div>
										<h2 data-testid="plan-name" className="mt-0.5 mb-1 text-[26px] font-bold">
											{p?.plan.name}
										</h2>
										<div className="text-[13px] text-muted-foreground">
											{me.data?.activated_at
												? fill(billed ? t.sinceBilled : t.since, {
														date: formatDate(me.data.activated_at),
														product: productName,
													})
												: fill(billed ? t.managedBilled : t.managed, { product: productName })}
										</div>
									</div>
									{me.data ? <StatusBadge status={me.data.status} /> : null}
								</div>
								<section aria-label={t.meters} data-testid="plan-meters">
									<UsageMeter
										label={plansStrings.usage.questions}
										used={p?.usage.questions ?? 0}
										limit={p?.limits.monthly_questions ?? null}
										hint={p ? questionsHint(p) : undefined}
									/>
									<UsageMeter
										label={plansStrings.usage.kbs}
										used={p?.usage.knowledge_bases ?? 0}
										limit={p?.limits.max_knowledge_bases ?? null}
										hint={kbNames.length ? kbNames.join(", ") : t.kbsNone}
									/>
									<UsageMeter
										label={plansStrings.usage.documents}
										used={p?.usage.documents ?? 0}
										limit={p?.limits.max_documents ?? null}
										hint={t.documentsHint}
									/>
									<UsageMeter
										label={plansStrings.usage.widgetKeys}
										used={p?.usage.widget_keys ?? 0}
										limit={p?.limits.max_widget_keys ?? null}
										hint={t.widgetHint}
									/>
								</section>
								<dl data-testid="plan-size-limits" className="m-0 border-t border-border px-4 py-1">
									{SIZE_KEYS.map((k) => (
										<div
											key={k}
											className="flex justify-between gap-3 border-b border-dashed border-border py-2.5 text-[13.5px] last:border-b-0"
										>
											<dt>{plansStrings.limits[k]}</dt>
											<dd className="m-0 font-mono font-medium">{formatSizeLimit(k, p?.limits[k])}</dd>
										</div>
									))}
								</dl>
							</>
						)}
					</section>
					{isMember ? null : <ChangePlanCard />} {/* P6 plan choice */}
				</div>
				<div className="grid content-start gap-4">
					{/* BIL6 */}
					{view && showMoney ? (
						<BalanceCard view={view} canRequest={!isMember} onRequestTopUp={() => setTopUpOpen(true)} />
					) : null}
					{view && billed ? <UsageThisPeriod view={view} questionLimit={p?.limits.monthly_questions ?? null} /> : null}
					{/* P1 payments */}
					{view && showMoney ? <PayCard view={view} canPay={!isMember} /> : null}
					{view && showMoney && !isMember ? <PaymentsCard /> : null}
					{/* /P1 payments */}
					{view && showMoney && !isMember ? <InvoicesCard /> : null} {/* P2 invoices */}
					{/* TV3 */}
					{view && showMoney ? <RenewalCard view={view} /> : null}
					{view && showMoney ? <SpendCapCard view={view} /> : null}
					{/* /TV3 */}
					{view && billed && !view.money_visible ? (
						<p
							data-testid="plan-member-note"
							className="m-0 rounded-lg border border-border bg-card px-4 py-3 text-[13px] text-muted-foreground"
						>
							{billing.memberNote}
						</p>
					) : null}
					{view && showMoney && !isMember ? <StatementsCard view={view} tenantName={tenant} /> : null}
					{/* /BIL6 */}
					<ActiveAddOns /> {/* BIL5 add-ons */}
					<ShopCard canBuy={!isMember} /> {/* P5 plans */}
					<OwnerServiceOrders /> {/* P5 plans */}
					<OwnerFairUseCard /> {/* PIP4 */}
					{billed ? null : <NeedMoreCard tenant={tenant} plan={p} />}
					<MonthByKb corpora={corpora.data?.items ?? []} />
				</div>
			</div>
			<RequestTopUpDialog open={topUpOpen} onOpenChange={setTopUpOpen} /> {/* BIL6 */}
		</div>
	);
}
