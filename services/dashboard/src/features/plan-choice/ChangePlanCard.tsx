import { useQuery } from "@tanstack/react-query";
import { Check } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { goToPay } from "@/features/payments/checkout";
import { RequestTopUpDialog } from "@/features/plan/RequestTopUpDialog";
import type { ChangePlanResponse, PendingPlanChange, PlanChoice, PlanChoiceList } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatDate } from "@/lib/format/date";
import { formatMinor } from "@/lib/format/money";
import { fill } from "@/lib/strings/en";
import { planChoiceStrings as s } from "@/lib/strings/plan-choice";
import { majorText, planChoicesQuery, useCancelPlanChange, useChangePlan } from "./api";

type Currency = PlanChoiceList["currency"];

/** "₹999.00 a month + GST · ₹1,178.82 incl. GST", or the fee alone when GST adds nothing. */
export function feeLine(p: PlanChoice, currency: Currency): string {
	if (!p.price) return "";
	const price = p.price;
	if (price.subscription_fee_minor <= 0) return s.free;
	const vars = {
		price: formatMinor(price.subscription_fee_minor, currency),
		per: s.perPeriod[price.billing_period],
		total: formatMinor(price.inclusive_total_minor, currency),
	};
	return price.inclusive_total_minor === price.subscription_fee_minor ? fill(s.priceFlat, vars) : fill(s.price, vars);
}

/** What moving to `p` now means, in one line under its button. */
export function moveNote(p: PlanChoice, currency: Currency): string {
	if (p.direction === "downgrade") return fill(s.downgradeNote, { date: formatDate(p.effective_on) });
	if (p.direction === "upgrade" && p.due_now_minor > 0) {
		return fill(p.first_period ? s.firstPeriodNote : s.upgradeNote, {
			amount: formatMinor(p.due_now_minor, currency),
		});
	}
	return s.freeNote;
}

function actionLabel(p: PlanChoice): string {
	if (p.direction === "upgrade") return fill(s.upgrade, { plan: p.name });
	if (p.direction === "downgrade") return fill(s.downgrade, { plan: p.name });
	return fill(s.switchTo, { plan: p.name });
}

function limit(v: number | null | undefined): string {
	return v == null ? s.includes.unlimited : v.toLocaleString("en-IN");
}

/**
 * Plan & billing → "Change plan" (P6, owners): the plans the workspace may move to, side by side
 * with their price (GST included beside it), what each includes and what a move means now: an
 * upgrade starts today and costs only the difference for the days left in the period (shown before
 * confirming), a downgrade starts at the renewal. A move waiting for its payment or the renewal is
 * shown on top, with "Pay" (online payments) or "Request a top-up" (manual), and can be called off.
 */
export function ChangePlanCard() {
	const q = useQuery(planChoicesQuery);
	const { productName } = useBranding();
	const change = useChangePlan();
	const cancel = useCancelPlanChange();
	const [picked, setPicked] = useState<PlanChoice | null>(null);
	const [calling, setCalling] = useState(false);
	const [topUp, setTopUp] = useState<{ amount: string; note: string } | null>(null);

	if (q.isPending) {
		return (
			<section aria-label={s.title} className="rounded-lg border border-border bg-card" aria-busy="true">
				<TableSkeleton rows={3} />
			</section>
		);
	}
	const data = q.data;
	if (q.isError || !data) return null;
	const others = data.items.filter((p) => !p.current);
	if (!data.can_change || (others.length === 0 && !data.pending)) return null;
	const currency = data.currency;
	const current = data.items.find((p) => p.current);

	const afterWaiting = (res: Pick<ChangePlanResponse, "pay_amount_minor" | "pay_online" | "plan_name">) => {
		if (res.pay_online) {
			goToPay({ purpose: "top_up", amount: res.pay_amount_minor });
			return;
		}
		setTopUp({ amount: majorText(res.pay_amount_minor), note: fill(s.pending.topUpNote, { plan: res.plan_name }) });
	};

	const confirm = async () => {
		if (!picked) return;
		try {
			const res = await change.mutateAsync(picked.id);
			setPicked(null);
			if (res.status === "applied") {
				toast.success(fill(s.done.applied, { plan: res.plan_name }), {
					description:
						res.charged_minor > 0
							? fill(s.done.appliedCharged, { amount: formatMinor(res.charged_minor, currency) })
							: undefined,
				});
			} else if (res.status === "scheduled") {
				toast.success(fill(s.done.scheduled, { plan: res.plan_name, date: formatDate(res.effective_on) }), {
					description: s.done.scheduledBody,
				});
			} else if (res.status === "awaiting_payment") {
				toast(fill(s.done.awaiting, { plan: res.plan_name }));
				afterWaiting(res);
			} else {
				toast(fill(s.done.current, { plan: res.plan_name }));
			}
		} catch (e) {
			setPicked(null);
			const m = messageFor(toApiError(e));
			toast.error(m.title, { description: m.body });
		}
	};

	const callOff = async () => {
		try {
			await cancel.mutateAsync();
			toast.success(s.done.cancelled, { description: s.done.cancelledBody });
		} catch (e) {
			toast.error(messageFor(toApiError(e)).title);
		}
		setCalling(false);
	};

	return (
		<section
			aria-label={s.title}
			data-testid="change-plan-card"
			className="min-w-0 rounded-lg border border-border bg-card"
		>
			<div className="border-b border-border px-4 py-3.5">
				<h2 className="text-[15px] font-bold">{s.title}</h2>
				<p className="m-0 text-[12.5px] text-muted-foreground">{s.lead}</p>
			</div>
			{data.pending ? (
				<PendingNotice
					pending={data.pending}
					currency={currency}
					payOnline={data.pay_online}
					product={productName}
					onPay={() =>
						data.pending &&
						afterWaiting({
							pay_amount_minor: data.pending.pay_amount_minor,
							pay_online: data.pay_online,
							plan_name: data.pending.plan_name,
						})
					}
					onCancel={() => setCalling(true)}
				/>
			) : null}
			<ul className="m-0 grid list-none gap-3 p-4 min-[700px]:grid-cols-2">
				{data.items.map((p) => (
					<PlanTile
						key={p.id}
						plan={p}
						currency={currency}
						product={productName}
						locked={Boolean(data.pending?.by_provider)}
						onPick={() => setPicked(p)}
					/>
				))}
			</ul>
			<ConfirmDialog
				open={picked !== null}
				onOpenChange={(o) => !o && setPicked(null)}
				title={picked ? confirmTitle(picked) : ""}
				body={
					picked ? (
						<ConfirmBody plan={picked} current={current} currency={currency} renewsOn={data.renews_on ?? null} />
					) : null
				}
				confirmLabel={picked ? confirmLabel(picked) : ""}
				destructive={false}
				pending={change.isPending}
				onConfirm={() => void confirm()}
			/>
			<ConfirmDialog
				open={calling}
				onOpenChange={setCalling}
				title={s.pending.cancelTitle}
				body={fill(s.pending.cancelBody, { plan: data.pending?.plan_name ?? "" })}
				confirmLabel={s.pending.cancelConfirm}
				destructive={false}
				pending={cancel.isPending}
				onConfirm={() => void callOff()}
			/>
			<RequestTopUpDialog
				open={topUp !== null}
				onOpenChange={(o) => !o && setTopUp(null)}
				initialAmount={topUp?.amount}
				initialNote={topUp?.note}
			/>
		</section>
	);
}

function confirmTitle(p: PlanChoice): string {
	if (p.direction === "upgrade") return fill(s.confirm.upgradeTitle, { plan: p.name });
	if (p.direction === "downgrade") return fill(s.confirm.downgradeTitle, { plan: p.name });
	return fill(s.confirm.switchTitle, { plan: p.name });
}

function confirmLabel(p: PlanChoice): string {
	if (p.direction === "upgrade") return p.affordable ? s.confirm.upgrade : s.confirm.payAndUpgrade;
	if (p.direction === "downgrade") return s.confirm.downgrade;
	return s.confirm.switchNow;
}

/** The proration, in plain words, before anything is charged. */
export function ConfirmBody({
	plan,
	current,
	currency,
	renewsOn,
}: {
	plan: PlanChoice;
	current: PlanChoice | undefined;
	currency: Currency;
	renewsOn: string | null;
}) {
	const amount = formatMinor(plan.due_now_minor, currency);
	if (plan.direction === "downgrade") {
		return (
			<p data-testid="plan-confirm-body" className="m-0">
				{fill(s.confirm.downgradeBody, {
					plan: plan.name,
					current: current?.name ?? "",
					date: formatDate(plan.effective_on),
				})}
			</p>
		);
	}
	if (plan.direction !== "upgrade" || plan.due_now_minor <= 0) {
		return (
			<p data-testid="plan-confirm-body" className="m-0">
				{fill(s.confirm.switchBody, { plan: plan.name })}
			</p>
		);
	}
	return (
		<div data-testid="plan-confirm-body" className="grid gap-2">
			<p className="m-0">
				{plan.first_period
					? fill(s.confirm.firstPeriodBody, { plan: plan.name, amount })
					: fill(s.confirm.upgradeBody, {
							plan: plan.name,
							amount,
							date: formatDate(renewsOn),
						})}
			</p>
			{plan.credit_now_minor > 0 ? (
				<p className="m-0">{fill(s.confirm.creditBody, { credit: formatMinor(plan.credit_now_minor, currency) })}</p>
			) : null}
			{plan.price ? <p className="m-0">{fill(s.confirm.renewalBody, { fee: feeLine(plan, currency) })}</p> : null}
			{plan.affordable ? null : (
				<p data-testid="plan-confirm-pay-first" className="m-0 font-semibold">
					{fill(s.confirm.payFirst, { amount, plan: plan.name })}
				</p>
			)}
		</div>
	);
}

function PlanTile({
	plan,
	currency,
	product,
	locked,
	onPick,
}: {
	plan: PlanChoice;
	currency: Currency;
	product: string;
	locked: boolean;
	onPick: () => void;
}) {
	const price = plan.price;
	return (
		<li
			data-testid="plan-choice"
			data-plan={plan.slug}
			className={`grid content-start gap-2 rounded-lg border px-4 py-3 ${plan.current ? "border-primary" : "border-border"}`}
		>
			<div className="flex flex-wrap items-baseline justify-between gap-2">
				<h3 className="m-0 text-[15px] font-bold">{plan.name}</h3>
				{plan.current ? <Badge variant="info">{s.current}</Badge> : null}
			</div>
			{price ? (
				<p data-testid="plan-choice-price" className="m-0 font-mono text-[13px] tabular-nums">
					{feeLine(plan, currency)}
				</p>
			) : null}
			{price && price.included_credit_minor > 0 ? (
				<p className="m-0 text-[13px] text-foreground-2">
					{fill(s.credit, {
						amount: formatMinor(price.included_credit_minor, currency),
						period: s.period[price.billing_period],
					})}
				</p>
			) : null}
			{plan.description ? <p className="m-0 text-[12.5px] text-muted-foreground">{plan.description}</p> : null}
			<dl className="m-0 grid gap-0.5 text-[12.5px]">
				{(
					[
						[s.includes.kbs, plan.includes.max_knowledge_bases],
						[s.includes.questions, plan.includes.monthly_questions],
						[s.includes.documents, plan.includes.max_documents],
						[s.includes.widgets, plan.includes.max_widget_keys],
					] as const
				).map(([label, value]) => (
					<div key={label} className="flex justify-between gap-2">
						<dt className="text-muted-foreground">{label}</dt>
						<dd className="m-0 font-medium">{limit(value)}</dd>
					</div>
				))}
			</dl>
			{plan.includes.own_ai_key ? (
				<p className="m-0 flex items-center gap-1.5 text-[12.5px]">
					<Check aria-hidden className="size-3.5 text-success" />
					{s.includes.ownKey}
				</p>
			) : null}
			{plan.current ? null : !plan.available ? (
				<small data-testid="plan-choice-unavailable" className="text-muted-foreground">
					{fill(s.unavailable[(plan.unavailable_reason ?? "needs_billing") as keyof typeof s.unavailable] ?? "", {
						product,
					})}
				</small>
			) : (
				<div className="grid gap-1 pt-1">
					<Button
						type="button"
						size="sm"
						variant={plan.direction === "upgrade" ? "default" : "outline"}
						disabled={locked}
						onClick={onPick}
					>
						{actionLabel(plan)}
					</Button>
					<small data-testid="plan-choice-note" className="text-muted-foreground">
						{moveNote(plan, currency)}
					</small>
					{plan.direction === "upgrade" && !plan.affordable ? (
						<small className="text-warning">{s.lowBalance}</small>
					) : null}
				</div>
			)}
		</li>
	);
}

function PendingNotice({
	pending,
	currency,
	payOnline,
	product,
	onPay,
	onCancel,
}: {
	pending: PendingPlanChange;
	currency: Currency;
	payOnline: boolean;
	product: string;
	onPay: () => void;
	onCancel: () => void;
}) {
	if (pending.kind === "scheduled") {
		return (
			<div data-testid="plan-pending" className="grid gap-2 border-b border-border bg-primary-soft/40 px-4 py-3">
				<p className="m-0 text-[13.5px] font-semibold">
					{pending.by_provider
						? fill(s.pending.byProvider, { product, date: formatDate(pending.effective_on) })
						: fill(s.pending.scheduled, { plan: pending.plan_name, date: formatDate(pending.effective_on) })}
				</p>
				{pending.by_provider ? null : (
					<div>
						<Button type="button" size="sm" variant="ghost" onClick={onCancel}>
							{s.pending.cancel}
						</Button>
					</div>
				)}
			</div>
		);
	}
	const pay = formatMinor(pending.pay_amount_minor, currency);
	return (
		<div data-testid="plan-pending" className="grid gap-2 border-b border-border bg-warning-soft/40 px-4 py-3">
			<p className="m-0 text-[13.5px] font-semibold">{fill(s.pending.awaiting, { plan: pending.plan_name })}</p>
			<p className="m-0 text-[13px] text-foreground-2">
				{fill(s.pending.awaitingAmount, { amount: formatMinor(pending.amount_due_minor, currency), pay })}
				{payOnline ? null : ` ${fill(s.pending.awaitingManual, { product })}`}
			</p>
			{pending.expires_at ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">
					{fill(s.pending.expires, { date: formatDate(pending.expires_at) })}
				</p>
			) : null}
			<div className="flex flex-wrap gap-2">
				<Button type="button" size="sm" variant="default" onClick={onPay}>
					{payOnline ? fill(s.pending.pay, { amount: pay }) : s.pending.requestTopUp}
				</Button>
				<Button type="button" size="sm" variant="ghost" onClick={onCancel}>
					{s.pending.cancel}
				</Button>
			</div>
		</div>
	);
}
