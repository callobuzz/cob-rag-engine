import { zodResolver } from "@hookform/resolvers/zod";
import { Search } from "lucide-react";
import { useId, useState } from "react";
import { z } from "zod";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { SubmitButton } from "@/components/forms/SubmitButton";
import { Input } from "@/components/ui/input";
import { messageFor } from "@/lib/api/error-messages";
import { formatNumber } from "@/lib/format/number";
import { useAppForm, useAppSubmit } from "@/lib/forms/app-form";
import { modelsStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { discoverOpenRouter, type ModelEntry } from "./api";
import { CatalogTable } from "./CatalogTable";

const t = modelsStrings.discover;
const keySchema = z.object({ key: z.string().trim().min(1, t.needKey) });

/** Case-insensitive match on name, provider or id. */
export function filterModels(models: ModelEntry[], text: string): ModelEntry[] {
	const q = text.trim().toLowerCase();
	if (!q) return models;
	return models.filter((m) => `${m.name} ${m.provider} ${m.id}`.toLowerCase().includes(q));
}

/**
 * OpenRouter discovery: the key is held in this component's state for one call and cleared as
 * soon as the call returns (never written to storage, a query cache or the URL).
 */
export function OpenRouterDiscover() {
	const ids = { key: useId(), filter: useId() };
	const [models, setModels] = useState<ModelEntry[] | null>(null);
	const [filter, setFilter] = useState("");
	const form = useAppForm<{ key: string }>({ resolver: zodResolver(keySchema), defaultValues: { key: "" } });
	const submit = useAppSubmit(form, async (v) => {
		try {
			const r = await discoverOpenRouter(v.key.trim());
			setModels(r.models);
		} finally {
			form.setValue("key", "");
		}
	});
	const keyError = form.formState.errors.key?.message;
	const failure = submit.error
		? submit.error.status === 502 || submit.error.status === 422
			? t.failed
			: messageFor(submit.error).title
		: null;

	const shown = models ? filterModels(models, filter) : [];
	return (
		<div className="grid gap-4 p-4">
			<form onSubmit={(e) => void submit.onSubmit(e)} noValidate className="grid gap-3" aria-label={t.title}>
				<p className="m-0 text-[13px] text-foreground-2">{t.lead}</p>
				{failure ? <FormAlert>{failure}</FormAlert> : null}
				<Field id={ids.key} label={t.key} error={keyError}>
					<div className="flex flex-wrap gap-2">
						<Input
							id={ids.key}
							type="password"
							autoComplete="off"
							spellCheck={false}
							className="min-w-[220px] flex-1 font-mono"
							placeholder={t.keyPlaceholder}
							aria-invalid={!!keyError || undefined}
							aria-describedby={describedBy(ids.key, { error: !!keyError })}
							{...form.register("key")}
						/>
						<SubmitButton
							variant="default"
							pending={submit.isSubmitting}
							locked={submit.locked}
							pendingLabel={t.finding}
						>
							<Search aria-hidden />
							{t.find}
						</SubmitButton>
					</div>
				</Field>
			</form>
			{models ? (
				<div className="grid gap-3">
					<div className="flex flex-wrap items-end justify-between gap-3">
						<div className="grid gap-1">
							<label htmlFor={ids.filter} className="text-[12.5px] font-semibold text-foreground-2">
								{t.filter}
							</label>
							<Input
								id={ids.filter}
								type="search"
								className="w-[260px] max-w-full"
								placeholder={t.filterPlaceholder}
								value={filter}
								onChange={(e) => setFilter(e.target.value)}
							/>
						</div>
						<span className="text-[13px] text-muted-foreground" aria-live="polite">
							{fill(t.found, { n: formatNumber(shown.length) })}
						</span>
					</div>
					<div className="overflow-x-auto rounded-md border border-border">
						<CatalogTable models={shown} caption={t.caption} />
					</div>
				</div>
			) : null}
		</div>
	);
}
