import { Ellipsis, Lock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuItem,
	DropdownMenuSeparator,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { planSpeedLine } from "@/features/fair-use/limits"; // PIP4
import type { Plan } from "@/lib/api/accounts-types";
import { formatNumber } from "@/lib/format/number";
import { plansStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { planFairUseStrings } from "@/lib/strings/pipelines"; // PIP4
import { shopStrings } from "@/lib/strings/shop"; // P5 plans
import { cn } from "@/lib/utils";
import { formatLimit, formatSizeLimit, LIMIT_KEYS, SIZE_KEYS } from "./limits";

export type PlanAction = "edit" | "default" | "toggle" | "delete";

/** Mockup `.card.plan`: name + badges, price note, the four limits, customers + actions. */
export function PlanCard({
	plan,
	builtIn,
	onAction,
}: {
	plan: Plan;
	builtIn: boolean;
	onAction: (action: PlanAction, plan: Plan) => void;
}) {
	const off = !plan.is_active;
	return (
		<article
			aria-labelledby={`plan-${plan.id}`}
			data-testid="plan-card"
			data-default={plan.is_default || undefined}
			className={cn(
				"flex min-w-0 flex-col rounded-lg border bg-card",
				plan.is_default ? "border-[color-mix(in_srgb,var(--primary)_55%,var(--border))]" : "border-border",
			)}
		>
			<div className={cn("grid gap-1.5 border-b border-border p-4", off && "opacity-70")}>
				<h2 id={`plan-${plan.id}`} className="flex flex-wrap items-center gap-2 text-lg font-bold">
					<span className="min-w-0 break-words">{plan.name}</span>
					{plan.is_default ? <Badge variant="info">{t.card.defaultBadge}</Badge> : null}
					{plan.is_internal ? (
						<Badge variant="outline" data-testid="plan-internal-badge">
							<Lock aria-hidden />
							{t.internal.badge}
						</Badge>
					) : null}
					{builtIn ? <Badge>{t.card.builtIn}</Badge> : null}
					{off ? <Badge variant="warn">{t.card.off}</Badge> : null}
				</h2>
				<div className="text-[13px] break-words text-foreground-2">{plan.description || t.card.noPrice}</div>
			</div>
			<dl className={cn("m-0 flex-1 px-4 py-1", off && "opacity-70")}>
				{LIMIT_KEYS.map((k) => (
					<div
						key={k}
						className="flex justify-between gap-3 border-b border-dashed border-border py-2.5 text-[13.5px] last:border-b-0"
					>
						<dt>{t.limits[k]}</dt>
						<dd className="m-0 font-mono font-medium">{formatLimit(plan[k])}</dd>
					</div>
				))}
				{SIZE_KEYS.map((k) => (
					<div
						key={k}
						className="flex justify-between gap-3 border-b border-dashed border-border py-2.5 text-[13.5px] last:border-b-0"
					>
						<dt>{t.limits[k]}</dt>
						<dd className="m-0 font-mono font-medium">{formatSizeLimit(k, plan[k])}</dd>
					</div>
				))}
				<div className="flex justify-between gap-3 border-b border-dashed border-border py-2.5 text-[13.5px] last:border-b-0">
					<dt>{t.card.cachedAnswers}</dt>
					<dd className="m-0 font-medium">{plan.count_cached_answers ? t.card.cachedCounted : t.card.cachedFree}</dd>
				</div>
				{/* BIZ3 */}
				<div className="flex justify-between gap-3 border-b border-dashed border-border py-2.5 text-[13.5px] last:border-b-0">
					<dt>{t.business.website.title}</dt>
					<dd data-testid="plan-website" className="m-0 font-medium">
						{!plan.website_learning_enabled
							? t.business.card.noWebsite
							: plan.website_pages_max == null
								? t.card.unlimited
								: fill(t.business.card.pages, { n: formatNumber(plan.website_pages_max) })}
					</dd>
				</div>
				{plan.dashboard_features?.advanced === false ? (
					<div className="flex justify-between gap-3 border-b border-dashed border-border py-2.5 text-[13.5px] last:border-b-0">
						<dt>{t.business.title}</dt>
						<dd data-testid="plan-simple" className="m-0 font-medium">
							{t.business.card.simple}
						</dd>
					</div>
				) : null}
				{/* /BIZ3 */}
				{/* P5 plans */}
				<div className="flex justify-between gap-3 border-b border-dashed border-border py-2.5 text-[13.5px] last:border-b-0">
					<dt>{shopStrings.admin.ownKeyRow}</dt>
					<dd data-testid="plan-own-key" className="m-0 font-medium">
						{plan.byok_allowed ? shopStrings.admin.planBadgeOn : shopStrings.admin.planBadgeOff}
					</dd>
				</div>
				{/* /P5 plans */}
				{/* PIP4 */}
				<div className="flex justify-between gap-3 border-b border-dashed border-border py-2.5 text-[13.5px] last:border-b-0">
					<dt>{planFairUseStrings.card.speed}</dt>
					<dd data-testid="plan-speed" className="m-0 text-right font-medium">
						{planSpeedLine(plan)}
					</dd>
				</div>
				{/* /PIP4 */}
			</dl>
			<div className="flex items-center justify-between gap-3 rounded-b-lg border-t border-border bg-secondary px-4 py-3 text-[12.5px] text-muted-foreground">
				<span>
					{plan.is_internal
						? t.internal.owner
						: plan.tenant_count === 1
							? t.card.customer
							: fill(t.card.customers, { count: formatNumber(plan.tenant_count) })}
				</span>
				{plan.is_internal ? (
					// Built in and locked: no edit, no switch-off, no delete (the engine refuses all three).
					<Tooltip>
						<TooltipTrigger asChild>
							<Button
								size="sm"
								variant="ghost"
								data-locked=""
								aria-disabled="true"
								aria-label={fill(t.internal.lockedLabel, { name: plan.name })}
								className="cursor-not-allowed opacity-70"
								onClick={(e) => e.preventDefault()}
							>
								<Lock aria-hidden />
								{t.card.edit}
							</Button>
						</TooltipTrigger>
						<TooltipContent>{t.internal.tooltip}</TooltipContent>
					</Tooltip>
				) : (
					<div className="flex gap-1.5">
						<Button
							size="sm"
							aria-label={fill(t.card.editFor, { name: plan.name })}
							onClick={() => onAction("edit", plan)}
						>
							{t.card.edit}
						</Button>
						<DropdownMenu modal={false}>
							<DropdownMenuTrigger asChild>
								<Button size="sm" variant="ghost" aria-label={fill(t.card.more, { name: plan.name })}>
									<Ellipsis aria-hidden />
								</Button>
							</DropdownMenuTrigger>
							<DropdownMenuContent align="end">
								<DropdownMenuItem disabled={plan.is_default || off} onSelect={() => onAction("default", plan)}>
									{t.card.makeDefault}
								</DropdownMenuItem>
								<DropdownMenuItem disabled={plan.is_default} onSelect={() => onAction("toggle", plan)}>
									{off ? t.card.switchOn : t.card.switchOff}
								</DropdownMenuItem>
								<DropdownMenuSeparator />
								<DropdownMenuItem variant="destructive" onSelect={() => onAction("delete", plan)}>
									{t.card.delete}
								</DropdownMenuItem>
							</DropdownMenuContent>
						</DropdownMenu>
					</div>
				)}
			</div>
		</article>
	);
}
