import { Globe, Pause, Play, X } from "lucide-react";
import { type FormEvent, useId, useState } from "react";
import { toast } from "sonner";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { toastSaveError } from "@/features/kbs/detail/shared";
import { usePrincipal } from "@/lib/auth/store";
import { can } from "@/lib/permissions";
import { fill } from "@/lib/strings/en";
import { planFeatureStrings as t } from "@/lib/strings/plan-features";
import { widgetStrings } from "@/lib/strings/widget";
import { useUpdateWidgetKey, type WidgetKey } from "./api";
import { SnippetBox } from "./SnippetBox";
import { checkOrigin, MAX_ORIGINS, siteLabel } from "./schemas";

const s = t.codeOnly;

/**
 * The widget page for a plan that shows only the code (`widget_code_only`): the snippet to paste,
 * the sites it works on (an owner adds and removes them) and a pause switch. Nothing else about the
 * widget can be changed here, and the engine refuses the rest for this plan too.
 */
export function CodeOnlyWidget({ corpusId, keys }: { corpusId: string; keys: WidgetKey[] }) {
	const update = useUpdateWidgetKey(corpusId);
	const owner = can(usePrincipal(), "widgetKey.manage");
	const save = async (k: WidgetKey, allowed_origins: string[]) => {
		try {
			await update.mutateAsync({ id: k.id, body: { allowed_origins } });
			return true;
		} catch (e) {
			toastSaveError(e);
			return false;
		}
	};
	const toggle = async (k: WidgetKey) => {
		try {
			const r = await update.mutateAsync({ id: k.id, body: { is_active: !k.is_active } });
			toast.success(r.is_active ? s.resumedToast : s.pausedToast);
		} catch (e) {
			toastSaveError(e);
		}
	};
	return (
		<div className="grid gap-4" data-testid="widget-code-only">
			<p className="m-0 text-foreground-2">{s.lead}</p>
			{keys.length === 0 ? (
				<p className="m-0 rounded-lg border border-border bg-card p-5 text-[13.5px] text-muted-foreground">{s.none}</p>
			) : (
				keys.map((k) => (
					<article key={k.id} aria-label={k.name} className="grid gap-3 rounded-lg border border-border bg-card p-4">
						<div className="flex flex-wrap items-center justify-between gap-2">
							<h2 className="text-[16px] font-bold break-words">{k.name}</h2>
							<div className="flex items-center gap-2">
								<Badge variant={k.is_active ? "ok" : "default"}>{k.is_active ? "Active" : "Paused"}</Badge>
								<OwnerGate action="widgetKey.manage">
									<Button size="sm" disabled={update.isPending} onClick={() => void toggle(k)}>
										{k.is_active ? <Pause aria-hidden /> : <Play aria-hidden />}
										{k.is_active ? s.pause : s.resume}
									</Button>
								</OwnerGate>
							</div>
						</div>
						<SnippetBox name={k.name} snippet={k.embed_snippet} />
						<div className="grid gap-1.5">
							<div className="text-[13px] font-semibold">{s.sites}</div>
							{k.allowed_origins.length === 0 ? (
								<p className="m-0 text-[13px] text-muted-foreground">{s.noSites}</p>
							) : (
								<ul aria-label={s.sites} className="m-0 flex list-none flex-wrap gap-1.5 p-0">
									{k.allowed_origins.map((o) => (
										<li
											key={o}
											className="inline-flex max-w-full items-center gap-1 rounded-full border border-border bg-secondary px-2.5 py-0.5 text-[12.5px]"
										>
											<Globe className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
											<span className="truncate">{siteLabel(o)}</span>
											{owner ? (
												<button
													type="button"
													disabled={update.isPending}
													aria-label={fill(s.removeSite, { origin: o })}
													onClick={() =>
														void save(
															k,
															k.allowed_origins.filter((x) => x !== o),
														)
													}
													className="grid size-5 place-items-center rounded-full text-muted-foreground hover:bg-card hover:text-foreground disabled:opacity-50"
												>
													<X className="size-3.5" aria-hidden />
												</button>
											) : null}
										</li>
									))}
								</ul>
							)}
							{owner ? (
								<AddSite origins={k.allowed_origins} pending={update.isPending} onAdd={(next) => save(k, next)} />
							) : null}
						</div>
					</article>
				))
			)}
		</div>
	);
}

/** One website address at a time, checked like the full widget page checks it. */
function AddSite({
	origins,
	pending,
	onAdd,
}: {
	origins: string[];
	pending: boolean;
	onAdd: (next: string[]) => Promise<boolean>;
}) {
	const id = useId();
	const [draft, setDraft] = useState("");
	const [error, setError] = useState<string | null>(null);
	const submit = async (e: FormEvent) => {
		e.preventDefault();
		if (!draft.trim()) return;
		const checked = checkOrigin(draft, origins);
		if (!checked.ok || checked.origin === "*") {
			setError(checked.ok ? s.anySite : checked.error);
			return;
		}
		if (origins.length >= MAX_ORIGINS) {
			setError(widgetStrings.form.sitesTooMany);
			return;
		}
		setError(null);
		if (await onAdd([...origins, checked.origin])) setDraft("");
	};
	return (
		<form onSubmit={(e) => void submit(e)} className="grid gap-1.5" noValidate>
			<div className="flex gap-2">
				<Input
					id={id}
					value={draft}
					disabled={pending}
					inputMode="url"
					autoComplete="off"
					spellCheck={false}
					className="font-mono"
					aria-label={s.addLabel}
					aria-invalid={error ? true : undefined}
					aria-describedby={error ? `${id}-error` : undefined}
					placeholder={widgetStrings.origins.placeholder}
					onChange={(e) => {
						setDraft(e.target.value);
						setError(null);
					}}
				/>
				<Button type="submit" disabled={pending}>
					{s.addButton}
				</Button>
			</div>
			{error ? (
				<small id={`${id}-error`} className="text-[12.5px] font-medium text-destructive">
					{error}
				</small>
			) : null}
		</form>
	);
}
