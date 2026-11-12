import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CircleAlert, CircleCheck, CircleHelp, CircleX, Plus, RefreshCw } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate, formatRelative } from "@/lib/format/date";
import { costStrings } from "@/lib/strings/costs";
import { fill } from "@/lib/strings/en";
import { ProviderDialog } from "./ProviderDialog";
import {
	type ProviderBalance,
	type ProviderState,
	providerPath,
	providersQuery,
	spendByModel,
	usd,
	useRefreshProvider,
} from "./providers";

const t = costStrings.providers;

const BADGE: Record<ProviderState, { variant: "ok" | "warn" | "bad" | "default"; icon: typeof CircleCheck }> = {
	ok: { variant: "ok", icon: CircleCheck },
	low: { variant: "warn", icon: AlertTriangle },
	empty: { variant: "bad", icon: CircleX },
	unknown: { variant: "default", icon: CircleHelp },
	not_configured: { variant: "default", icon: CircleAlert },
};

export function StatusPill({ status }: { status: ProviderState }) {
	const m = BADGE[status];
	const Icon = m.icon;
	return (
		<Badge variant={m.variant} data-testid="provider-status" data-status={status}>
			<Icon aria-hidden />
			{t.status[status]}
		</Badge>
	);
}

function Line({ label, value }: { label: string; value: string }) {
	return (
		<div className="flex items-baseline justify-between gap-3 text-[13px]">
			<span className="text-muted-foreground">{label}</span>
			<span className="font-semibold tabular-nums">{value}</span>
		</div>
	);
}

function resetText(p: ProviderBalance): string | null {
	if (!p.limit_reset) return null;
	const base = t.resets[p.limit_reset];
	return p.resets_at ? fill(t.resetsOn, { when: base, date: formatDate(p.resets_at) }) : base;
}

function ProviderCard({ p, onEdit }: { p: ProviderBalance; onEdit: (p: ProviderBalance) => void }) {
	const refresh = useRefreshProvider();
	const r = p;
	const reset = resetText(p);
	const byModel = spendByModel(p.official_spend, `${p.checked_at.slice(0, 7)}-01`);
	const hasSpend = r.spend_today_usd_micro != null || r.spend_week_usd_micro != null || r.spend_month_usd_micro != null;
	const hasEstimate = p.manual_remaining_usd_micro != null;
	const ask = async () => {
		try {
			await refresh.mutateAsync(providerPath(p));
			toast.success(fill(t.refreshed, { provider: p.label }));
		} catch (e) {
			toast.error(messageFor(toApiError(e)).title);
		}
	};

	if (p.status === "not_configured") {
		return (
			<div
				className="grid content-start gap-2 rounded-lg border border-dashed border-border bg-card p-4"
				data-testid="provider-card"
				data-provider={p.provider}
			>
				<div className="flex items-center justify-between gap-2">
					<h3 className="text-[14px] font-bold">{p.label}</h3>
					<StatusPill status="not_configured" />
				</div>
				<p className="m-0 text-[12.5px] text-muted-foreground">{fill(t.notConfiguredBody, { provider: p.label })}</p>
				<div>
					<Button
						size="sm"
						variant="outline"
						aria-label={fill(t.editLabel, { provider: p.label })}
						onClick={() => onEdit(p)}
					>
						{t.edit}
					</Button>
				</div>
			</div>
		);
	}

	return (
		<div
			className="grid content-start gap-3 rounded-lg border border-border bg-card p-4"
			data-testid="provider-card"
			data-provider={p.provider}
			data-status={p.status}
		>
			<div className="flex items-center justify-between gap-2">
				<h3 className="text-[14px] font-bold">{p.label}</h3>
				<StatusPill status={p.status} />
			</div>
			<div>
				<div className="text-[12.5px] text-foreground-2">
					{hasEstimate && r.remaining_usd_micro === p.manual_remaining_usd_micro ? t.estimate : t.left}
				</div>
				<div className="font-display text-[26px] font-bold tracking-[-.02em] tabular-nums" data-testid="provider-left">
					{p.remaining_usd_micro == null ? t.unknownLeft : usd(p.remaining_usd_micro)}
				</div>
				{r.limit_usd_micro != null ? (
					<div className="text-[12.5px] text-muted-foreground">
						{fill(t.limit, { amount: usd(r.limit_usd_micro) })}
						{reset ? ` · ${reset}` : ""}
					</div>
				) : null}
				{p.low_threshold_usd_micro != null ? (
					<div className="text-[12.5px] text-muted-foreground">
						{fill(t.threshold, { amount: usd(p.low_threshold_usd_micro) })}
					</div>
				) : null}
			</div>

			{p.error ? (
				<p role="status" className="m-0 text-[12.5px] text-muted-foreground">
					{fill(t.errorAsking, { provider: p.label, error: p.error })}
				</p>
			) : null}

			{hasSpend ? (
				<div className="grid gap-1">
					<Line label={t.today} value={usd(r.spend_today_usd_micro)} />
					<Line label={t.week} value={usd(r.spend_week_usd_micro)} />
					<Line label={t.month} value={usd(r.spend_month_usd_micro)} />
					{r.account_credits_usd_micro != null ? (
						<Line label={t.credits} value={usd(r.account_credits_usd_micro)} />
					) : null}
				</div>
			) : null}

			{byModel.length > 0 ? (
				<div className="grid gap-1" data-testid="provider-by-model">
					<div className="text-[12.5px] font-semibold text-foreground-2">{t.byModel}</div>
					{byModel.slice(0, 5).map((m) => (
						<Line key={m.model ?? "other"} label={m.model ?? "—"} value={usd(m.usd_micro)} />
					))}
					<small className="text-[11.5px] text-muted-foreground">{fill(t.officialSpend, { provider: p.label })}</small>
				</div>
			) : null}

			{hasEstimate && p.manual_balance_usd_micro != null && p.manual_since ? (
				<div className="grid gap-0.5 text-[12.5px] text-muted-foreground" data-testid="provider-estimate">
					<span>
						{fill(t.estimateBasis, { amount: usd(p.manual_balance_usd_micro), date: formatDate(p.manual_since) })}
					</span>
					{!p.spend_tracked ? <span>{t.notTracked}</span> : null}
					{p.unpriced_requests > 0 ? <span>{fill(t.unpriced, { n: String(p.unpriced_requests) })}</span> : null}
				</div>
			) : null}
			{p.manual_note ? (
				<p className="m-0 text-[12.5px] text-muted-foreground">
					{t.note}: {p.manual_note}
				</p>
			) : null}

			{p.spend_tracked && p.recorded_month_usd_micro > 0 ? (
				<Line label={t.recorded} value={usd(p.recorded_month_usd_micro)} />
			) : null}

			<div className="flex flex-wrap items-center justify-between gap-2">
				<span className="text-[12px] text-muted-foreground">
					{p.provider === "manual"
						? ""
						: p.admin_key_source === "env"
							? t.keyFromEnv
							: p.admin_key_set
								? t.keySaved
								: t.noKey}
					{p.provider === "manual" ? "" : " · "}
					{fill(t.checkedAt, { when: formatRelative(p.checked_at) })}
				</span>
				<div className="flex gap-2">
					<Button
						size="sm"
						variant="ghost"
						aria-label={fill(t.refreshLabel, { provider: p.label })}
						onClick={() => void ask()}
						aria-busy={refresh.isPending || undefined}
					>
						<RefreshCw aria-hidden />
						{refresh.isPending ? t.refreshing : t.refresh}
					</Button>
					<Button
						size="sm"
						variant="outline"
						aria-label={fill(t.editLabel, { provider: p.label })}
						onClick={() => onEdit(p)}
					>
						{t.edit}
					</Button>
				</div>
			</div>
		</div>
	);
}

/**
 * Provider balances on the Costs page: one card per provider the super admin can see money for
 * (OpenRouter in full, OpenAI and Anthropic through an optional admin key, any number of manual
 * balances), with Edit and Refresh, and "Add a provider balance" for a provider with no API.
 */
export function ProviderBalancesPanel() {
	const q = useQuery(providersQuery);
	const [editing, setEditing] = useState<ProviderBalance | null>(null);
	const [open, setOpen] = useState(false);

	const edit = (p: ProviderBalance | null) => {
		setEditing(p);
		setOpen(true);
	};
	// OpenRouter only shows when the engine uses it or something is saved for it.
	const items = (q.data?.items ?? []).filter(
		(p) => !(p.provider === "openrouter" && p.status === "not_configured" && !p.id),
	);

	return (
		<>
			<Panel
				title={t.title}
				sub={t.lead}
				aside={
					<Button size="sm" variant="outline" onClick={() => edit(null)}>
						<Plus aria-hidden />
						{t.add}
					</Button>
				}
			>
				<div className="p-4">
					{q.isError ? (
						<ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} />
					) : !q.data ? (
						<CardSkeleton lines={4} />
					) : (
						<div
							className="grid grid-cols-3 gap-4 max-[1100px]:grid-cols-2 max-[600px]:grid-cols-1"
							data-testid="provider-balances"
						>
							{items.map((p) => (
								<ProviderCard key={p.id ?? p.provider} p={p} onEdit={edit} />
							))}
						</div>
					)}
				</div>
			</Panel>
			<ProviderDialog open={open} onOpenChange={setOpen} provider={editing} />
		</>
	);
}
