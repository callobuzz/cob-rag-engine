import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Input } from "@/components/ui/input";
import { type Settings, settingsQuery, useSaveSettings } from "@/features/branding/api";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatMoney, useCurrency } from "@/lib/format/currency";
import { fill } from "@/lib/strings/en";
import { railsStrings as t } from "@/lib/strings/pipelines";
import { microToUsd, parseCount, usdToMicro } from "./limits";

type Rails = Settings["pipelines"];

interface Draft {
	spend: string;
	alert: string;
	crawl: string;
}

/** An engine that does not report rails yet: no ceiling, warn at 80 %, no crawl limit. */
const NO_RAILS: Rails = {
	engine_ai_spend_per_day_usd_micro: null,
	engine_spend_alert_pct: 80,
	crawl_concurrency: null,
};

const draftOf = (r: Rails): Draft => ({
	spend: microToUsd(r.engine_ai_spend_per_day_usd_micro),
	alert: String(r.engine_spend_alert_pct),
	crawl: r.crawl_concurrency == null ? "" : String(r.crawl_concurrency),
});

export interface RailsPatch {
	engine_ai_spend_per_day_usd_micro?: number | null;
	engine_spend_alert_pct?: number;
	crawl_concurrency?: number | null;
}

/**
 * What changed, as the PATCH `pipelines` block (dollars to integer micro, empty to null so the
 * engine goes back to no ceiling / no limit), or the problems found. Unchanged fields are not sent.
 */
export function railsPatch(
	before: Rails,
	d: Draft,
): { patch: RailsPatch; errors: Partial<Record<keyof Draft, string>> } {
	const patch: RailsPatch = {};
	const errors: Partial<Record<keyof Draft, string>> = {};
	const spend = usdToMicro(d.spend);
	if (spend === undefined) errors.spend = t.invalidSpend;
	else if (spend !== (before.engine_ai_spend_per_day_usd_micro ?? null))
		patch.engine_ai_spend_per_day_usd_micro = spend;
	const alert = Number(d.alert);
	if (!/^\d+$/.test(d.alert.trim()) || alert < 1 || alert > 100) errors.alert = t.invalidAlert;
	else if (alert !== before.engine_spend_alert_pct) patch.engine_spend_alert_pct = alert;
	const crawl = parseCount(d.crawl);
	if (crawl === undefined || (crawl != null && crawl > 64)) errors.crawl = t.invalidCrawl;
	else if (crawl !== (before.crawl_concurrency ?? null)) patch.crawl_concurrency = crawl;
	return { patch, errors };
}

/** Engine page: the AI spend ceiling, when to warn, and how many website reads run at once. */
export function SafetyRailsCard() {
	const q = useQuery(settingsQuery);
	return (
		<Panel title={t.title} sub={t.lead}>
			{q.isPending ? (
				<CardSkeleton lines={3} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
				</div>
			) : (
				<RailsForm key={JSON.stringify(q.data.pipelines ?? null)} rails={q.data.pipelines ?? NO_RAILS} />
			)}
		</Panel>
	);
}

function RailsForm({ rails }: { rails: Rails }) {
	const ids = { spend: useId(), alert: useId(), crawl: useId() };
	const save = useSaveSettings();
	const [d, setD] = useState<Draft>(draftOf(rails));
	const [errors, setErrors] = useState<Partial<Record<keyof Draft, string>>>({});
	const [alert, setAlert] = useState<string | null>(null);
	useCurrency();
	const micro = usdToMicro(d.spend);

	const submit = async (ev: React.FormEvent) => {
		ev.preventDefault();
		setAlert(null);
		const { patch, errors: found } = railsPatch(rails, d);
		setErrors(found);
		if (Object.keys(found).length > 0) return;
		if (Object.keys(patch).length === 0) return void toast(t.nothingChanged);
		try {
			await save.mutateAsync({ pipelines: patch });
			toast.success(t.saved);
		} catch (e) {
			setAlert(messageFor(toApiError(e)).title);
		}
	};

	return (
		<form onSubmit={(e) => void submit(e)} noValidate>
			<div className="grid gap-3 p-4">
				{alert ? <FormAlert>{alert}</FormAlert> : null}
				<Field
					id={ids.spend}
					label={t.spend}
					hint={micro ? fill(t.spendHintInr, { inr: formatMoney(micro / 1_000_000, "INR") }) : t.spendHint}
					error={errors.spend}
				>
					<Input
						id={ids.spend}
						inputMode="decimal"
						autoComplete="off"
						className="font-mono"
						placeholder="No ceiling"
						aria-invalid={errors.spend ? true : undefined}
						aria-describedby={describedBy(ids.spend, { hint: true, error: !!errors.spend })}
						value={d.spend}
						onChange={(e) => setD({ ...d, spend: e.target.value })}
					/>
				</Field>
				<Field id={ids.alert} label={t.alert} hint={t.alertHint} error={errors.alert}>
					<Input
						id={ids.alert}
						inputMode="numeric"
						autoComplete="off"
						className="font-mono"
						aria-invalid={errors.alert ? true : undefined}
						aria-describedby={describedBy(ids.alert, { hint: true, error: !!errors.alert })}
						value={d.alert}
						onChange={(e) => setD({ ...d, alert: e.target.value })}
					/>
				</Field>
				<Field id={ids.crawl} label={t.crawl} hint={t.crawlHint} error={errors.crawl}>
					<Input
						id={ids.crawl}
						inputMode="numeric"
						autoComplete="off"
						className="font-mono"
						placeholder="No limit"
						aria-invalid={errors.crawl ? true : undefined}
						aria-describedby={describedBy(ids.crawl, { hint: true, error: !!errors.crawl })}
						value={d.crawl}
						onChange={(e) => setD({ ...d, crawl: e.target.value })}
					/>
				</Field>
			</div>
			<FormFoot>
				<SubmitButton variant="default" pending={save.isPending}>
					{t.save}
				</SubmitButton>
			</FormFoot>
		</form>
	);
}
