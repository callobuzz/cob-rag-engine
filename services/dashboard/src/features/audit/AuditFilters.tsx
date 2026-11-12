import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";
import { FormAlert } from "@/components/auth/FormAlert";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { auditStrings } from "@/lib/strings/admin";
import { ACTION_PREFIXES, AUDIT_ACTIONS, actionLabel, prefixLabel } from "./actions";
import { auditTenantsQuery } from "./api";
import { type AuditSearch, rangeProblem } from "./search";

const t = auditStrings.filters;

/**
 * Filters in one row above the table, all in the URL: action (catalog action or prefix group),
 * customer (selects apply at once), who and the date range (applied together).
 */
export function AuditFilters({
	search,
	onChange,
	serverError,
}: {
	search: AuditSearch;
	onChange: (next: Partial<AuditSearch>) => void;
	/** The engine's 422 on the filters (unknown action, range too long). */
	serverError: string | null;
}) {
	const ids = { action: useId(), tenant: useId(), actor: useId(), from: useId(), to: useId() };
	const tenants = useQuery(auditTenantsQuery);
	const [actor, setActor] = useState(search.actor ?? "");
	const [from, setFrom] = useState(search.from ?? "");
	const [to, setTo] = useState(search.to ?? "");
	const [problem, setProblem] = useState<string | null>(null);
	useEffect(() => setActor(search.actor ?? ""), [search.actor]);
	useEffect(() => setFrom(search.from ?? ""), [search.from]);
	useEffect(() => setTo(search.to ?? ""), [search.to]);

	const apply = (e: React.FormEvent) => {
		e.preventDefault();
		const p = rangeProblem(from || undefined, to || undefined);
		if (p) {
			setProblem(t[p]);
			return;
		}
		setProblem(null);
		onChange({ actor: actor.trim() || undefined, from: from || undefined, to: to || undefined });
	};

	const tenantItems = [...(tenants.data?.items ?? [])].sort((a, b) => a.name.localeCompare(b.name, "en-IN"));
	const knownTenant = !search.tenant_id || tenantItems.some((x) => x.id === search.tenant_id);
	const knownAction =
		!search.action || AUDIT_ACTIONS.includes(search.action as never) || ACTION_PREFIXES.includes(search.action);
	const filtered = !!(search.action || search.tenant_id || search.actor || search.from || search.to);
	const label = "text-[12.5px] font-semibold text-foreground-2";

	return (
		<form
			aria-label={t.label}
			onSubmit={apply}
			noValidate
			className="grid gap-3 rounded-lg border border-border bg-card p-3"
		>
			<div className="grid items-end gap-3 min-[861px]:grid-cols-[repeat(3,minmax(0,1fr))] min-[1101px]:grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_minmax(0,1fr)_150px_150px_auto]">
				<div className="grid gap-1">
					<label htmlFor={ids.action} className={label}>
						{t.action}
					</label>
					<NativeSelect
						id={ids.action}
						value={search.action ?? ""}
						onChange={(e) => onChange({ action: e.target.value || undefined })}
					>
						<option value="">{t.allActions}</option>
						{!knownAction && search.action ? <option value={search.action}>{search.action}</option> : null}
						<optgroup label={t.groups}>
							{ACTION_PREFIXES.map((p) => (
								<option key={p} value={p}>
									{prefixLabel(p)} ({p}*)
								</option>
							))}
						</optgroup>
						<optgroup label={t.single}>
							{AUDIT_ACTIONS.map((a) => (
								<option key={a} value={a}>
									{actionLabel(a)}
								</option>
							))}
						</optgroup>
					</NativeSelect>
				</div>
				<div className="grid gap-1">
					<label htmlFor={ids.tenant} className={label}>
						{t.customer}
					</label>
					<NativeSelect
						id={ids.tenant}
						value={search.tenant_id ?? ""}
						onChange={(e) => onChange({ tenant_id: e.target.value || undefined })}
					>
						<option value="">{t.allCustomers}</option>
						{!knownTenant && search.tenant_id ? <option value={search.tenant_id}>{search.tenant_id}</option> : null}
						{tenantItems.map((x) => (
							<option key={x.id} value={x.id}>
								{x.name}
							</option>
						))}
					</NativeSelect>
				</div>
				<div className="grid gap-1">
					<label htmlFor={ids.actor} className={label}>
						{t.actor}
					</label>
					<Input
						id={ids.actor}
						type="search"
						autoComplete="off"
						placeholder={t.actorPlaceholder}
						maxLength={320}
						value={actor}
						onChange={(e) => setActor(e.target.value)}
					/>
				</div>
				<div className="grid gap-1">
					<label htmlFor={ids.from} className={label}>
						{t.from}
					</label>
					<Input id={ids.from} type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
				</div>
				<div className="grid gap-1">
					<label htmlFor={ids.to} className={label}>
						{t.to}
					</label>
					<Input id={ids.to} type="date" value={to} onChange={(e) => setTo(e.target.value)} />
				</div>
				<div className="flex gap-2">
					<Button type="submit" variant="default">
						{t.apply}
					</Button>
					{filtered ? (
						<Button
							type="button"
							variant="ghost"
							onClick={() => {
								setProblem(null);
								onChange({ action: undefined, tenant_id: undefined, actor: undefined, from: undefined, to: undefined });
							}}
						>
							{t.clear}
						</Button>
					) : null}
				</div>
			</div>
			{(problem ?? serverError) ? <FormAlert>{problem ?? serverError}</FormAlert> : null}
		</form>
	);
}
