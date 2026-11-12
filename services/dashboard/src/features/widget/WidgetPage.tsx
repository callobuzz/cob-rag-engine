import { useQuery } from "@tanstack/react-query";
import { getRouteApi, Link, useNavigate } from "@tanstack/react-router";
import { Lock, MousePointerClick, Plus } from "lucide-react";
import { useCallback, useMemo, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KbScopedPage } from "@/components/patterns/KbScopedPage";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Button } from "@/components/ui/button";
import { planMeQuery } from "@/features/kbs/api";
import { toastSaveError } from "@/features/kbs/detail/shared";
import { FormFoot, Panel } from "@/features/kbs/ui";
import { toApiError } from "@/lib/api/errors";
import type { Corpus } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { useDashboardFeatures } from "@/lib/features"; // BIZ3
import { formatNumber } from "@/lib/format/number";
import { can } from "@/lib/permissions";
import { fill } from "@/lib/strings/en";
import { widgetStrings as t } from "@/lib/strings/widget";
import { type AppearanceErrors, AppearanceFields } from "./AppearanceFields";
import { useDeleteWidgetKey, useRotateWidgetKey, useUpdateWidgetKey, useWidgetKeys, type WidgetKey } from "./api";
import { CodeOnlyWidget } from "./CodeOnlyWidget"; // BIZ3
import { MockBubble } from "./MockBubble";
import { PreviewFrame } from "./PreviewFrame";
import { type AppearanceForm, appearanceSchema, appearanceToBody, appearanceToForm, sitesSentence } from "./schemas";
import { type CardAction, WidgetKeyCard } from "./WidgetKeyCard";
import { WidgetKeyForm } from "./WidgetKeyForm";

const route = getRouteApi("/_app/widget");

/** `/widget?kb=&key=` (D6): widget keys of one knowledge base, live preview and appearance. */
export function WidgetPage() {
	return (
		<KbScopedPage title={t.page.title} emptyBody={t.page.noKbBody}>
			{(kb) => <Widget key={kb.id} kb={kb} />}
		</KbScopedPage>
	);
}

type Confirm = { kind: "rotate" | "delete"; key: WidgetKey } | null;

function Widget({ kb }: { kb: Corpus }) {
	const { key: selectedId } = route.useSearch();
	const navigate = useNavigate();
	const principal = usePrincipal();
	const dashboard = useDashboardFeatures(); // BIZ3
	const keys = useWidgetKeys(kb.id);
	const update = useUpdateWidgetKey(kb.id);
	const rotate = useRotateWidgetKey(kb.id);
	const remove = useDeleteWidgetKey(kb.id);
	const [formOpen, setFormOpen] = useState(false);
	const [editing, setEditing] = useState<WidgetKey | null>(null);
	const [confirm, setConfirm] = useState<Confirm>(null);

	const select = useCallback(
		(id: string | undefined) => void navigate({ to: "/widget", search: { kb: kb.id, key: id }, replace: true }),
		[navigate, kb.id],
	);
	const list = keys.data ?? [];
	const selected = useMemo(() => list.find((k) => k.id === selectedId) ?? null, [list, selectedId]);

	const gone = (e: unknown) => {
		if (toApiError(e).status === 404) void keys.refetch();
		toastSaveError(e);
	};

	const onAction = async (k: WidgetKey, action: CardAction) => {
		if (action === "edit") {
			setEditing(k);
			setFormOpen(true);
		} else if (action === "toggle") {
			try {
				const r = await update.mutateAsync({ id: k.id, body: { is_active: !k.is_active } });
				if (r.is_active) toast.success(fill(t.card.resumedToast, { name: r.name }));
				else toast.success(fill(t.card.pausedToast, { name: r.name }), { description: t.card.pausedBody });
			} catch (e) {
				gone(e);
			}
		} else setConfirm({ kind: action, key: k });
	};

	const doConfirm = async () => {
		if (!confirm) return;
		const k = confirm.key;
		try {
			if (confirm.kind === "rotate") {
				await rotate.mutateAsync(k.id);
				toast.success(fill(t.rotate.toast, { name: k.name }), { description: t.rotate.toastBody });
			} else {
				await remove.mutateAsync(k.id);
				toast.success(fill(t.remove.toast, { name: k.name }));
				if (selectedId === k.id) select(undefined);
			}
		} catch (e) {
			gone(e);
		} finally {
			setConfirm(null);
		}
	};

	// BIZ3: a plan that shows only the code gets the snippet and the allowed sites, nothing else
	if (dashboard.codeOnlyWidget) {
		return (
			<>
				<PageHeader title={t.page.title} description={fill(t.page.lead, { kb: kb.name })} />
				{keys.isPending ? (
					<CardSkeleton lines={4} />
				) : keys.isError ? (
					<ErrorState error={keys.error} onRetry={() => void keys.refetch()} />
				) : (
					<CodeOnlyWidget corpusId={kb.id} keys={list} />
				)}
			</>
		);
	}
	// /BIZ3

	const newKeyButton = (label: string) => (
		<OwnerGate action="widgetKey.manage">
			<Button
				type="button"
				variant="default"
				onClick={() => {
					setEditing(null);
					setFormOpen(true);
				}}
			>
				<Plus aria-hidden />
				{label}
			</Button>
		</OwnerGate>
	);

	return (
		<>
			<PageHeader
				title={t.page.title}
				description={fill(t.page.lead, { kb: kb.name })}
				actions={list.length ? newKeyButton(t.page.newKey) : null}
			/>
			{principal?.view === "tenant" ? <PlanNote /> : null}
			{keys.isPending ? (
				<div className="grid gap-4" aria-busy="true">
					<CardSkeleton lines={4} />
					<CardSkeleton lines={3} />
				</div>
			) : keys.isError ? (
				<ErrorState error={keys.error} onRetry={() => void keys.refetch()} />
			) : list.length === 0 ? (
				<EmptyState
					title={t.page.emptyTitle}
					body={t.page.emptyBody}
					primaryAction={newKeyButton(t.page.emptyAction)}
					art={
						<ul className="m-0 grid list-none gap-2.5 p-0">
							{t.page.ways.map((w) => (
								<li key={w.b} className="rounded-md border border-border bg-muted px-4 py-3">
									<b className="block text-[14px]">{w.b}</b>
									<span className="text-[13px] text-foreground-2">{w.t}</span>
								</li>
							))}
						</ul>
					}
				/>
			) : (
				<div className="grid items-start gap-4 min-[1101px]:grid-cols-[minmax(0,1fr)_minmax(0,1.15fr)]">
					<section aria-label={t.page.listLabel} className="grid min-w-0 gap-4">
						{list.map((k) => (
							<WidgetKeyCard
								key={k.id}
								widgetKey={k}
								selected={selected?.id === k.id}
								busy={update.isPending && update.variables?.id === k.id}
								onSelect={() => select(selected?.id === k.id ? undefined : k.id)}
								onAction={(a) => void onAction(k, a)}
							/>
						))}
					</section>
					{selected ? (
						<div className="grid min-w-0 gap-4">
							<PreviewFrame corpusId={kb.id} widgetKey={selected} />
							<AppearancePanel
								key={`${selected.id}:${JSON.stringify(selected.appearance)}`}
								corpusId={kb.id}
								widgetKey={selected}
							/>
						</div>
					) : (
						<section className="grid min-h-[220px] place-content-center justify-items-center gap-2 rounded-lg border border-dashed border-input bg-card p-8 text-center">
							<MousePointerClick className="size-8 text-primary" aria-hidden />
							<h2 className="text-[17px] font-bold">{t.page.pickTitle}</h2>
							<p className="m-0 max-w-[360px] text-[13px] text-foreground-2">{t.page.pickBody}</p>
						</section>
					)}
				</div>
			)}
			<WidgetKeyForm
				corpusId={kb.id}
				editing={editing}
				open={formOpen}
				onOpenChange={setFormOpen}
				onCreated={(k) => select(k.id)}
			/>
			<ConfirmDialog
				open={confirm?.kind === "rotate"}
				onOpenChange={(o) => (o ? undefined : setConfirm(null))}
				title={fill(t.rotate.title, { name: confirm?.key.name ?? "" })}
				body={fill(t.rotate.body, { sites: sitesSentence(confirm?.key.allowed_origins ?? []) })}
				confirmLabel={t.rotate.confirm}
				pending={rotate.isPending}
				onConfirm={() => void doConfirm()}
			/>
			<ConfirmDialog
				open={confirm?.kind === "delete"}
				onOpenChange={(o) => (o ? undefined : setConfirm(null))}
				title={fill(t.remove.title, { name: confirm?.key.name ?? "" })}
				body={fill(t.remove.body, { sites: sitesSentence(confirm?.key.allowed_origins ?? []) })}
				confirmLabel={t.remove.confirm}
				pending={remove.isPending}
				onConfirm={() => void doConfirm()}
			/>
		</>
	);
}

/** "{used} of {limit} widget keys on your plan" (A6); hidden on error or when unlimited. */
function PlanNote() {
	const plan = useQuery(planMeQuery);
	const limit = plan.data?.limits.max_widget_keys;
	if (!plan.data || limit == null) return null;
	return (
		<p className="m-0 flex flex-wrap items-center gap-x-2 text-[13px] text-foreground-2" data-testid="plan-note">
			<span>
				{fill(t.page.planNote, {
					used: formatNumber(plan.data.usage.widget_keys),
					limit: formatNumber(limit),
				})}
			</span>
			<Link to="/plan" className="font-semibold">
				{t.page.planLink}
			</Link>
		</p>
	);
}

/** Appearance of the selected key: edits show in the mock bubble; Save reloads the live preview. */
function AppearancePanel({ corpusId, widgetKey }: { corpusId: string; widgetKey: WidgetKey }) {
	const principal = usePrincipal();
	const owner = can(principal, "widgetKey.manage");
	const update = useUpdateWidgetKey(corpusId);
	const saved = useMemo(() => appearanceToForm(widgetKey.appearance), [widgetKey.appearance]);
	const [value, setValue] = useState<AppearanceForm>(saved);
	const [errors, setErrors] = useState<AppearanceErrors>({});
	const dirty = JSON.stringify(value) !== JSON.stringify(saved);

	const save = async () => {
		const parsed = appearanceSchema.safeParse(value);
		if (!parsed.success) {
			const next: AppearanceErrors = {};
			for (const issue of parsed.error.issues) {
				const f = issue.path[0] as keyof AppearanceForm | undefined;
				if (f && !next[f]) next[f] = issue.message;
			}
			setErrors(next);
			return;
		}
		try {
			await update.mutateAsync({ id: widgetKey.id, body: { appearance: appearanceToBody(parsed.data, true) } });
			toast.success(t.appearance.savedToast, { description: t.appearance.savedBody });
		} catch (e) {
			const err = toApiError(e);
			const fieldErrs = err.fieldErrors.filter((f) => f.path.startsWith("appearance."));
			if (err.status === 422 && fieldErrs.length) {
				setErrors(Object.fromEntries(fieldErrs.map((f) => [f.path.split(".")[1], f.message])) as AppearanceErrors);
			} else toastSaveError(e);
		}
	};

	return (
		<Panel title={t.appearance.title} sub={t.appearance.sub}>
			<div className="grid items-start gap-5 p-4 min-[600px]:grid-cols-[minmax(0,1fr)_240px]">
				<AppearanceFields
					value={value}
					errors={errors}
					disabled={!owner}
					onChange={(patch) => {
						setValue((v) => ({ ...v, ...patch }));
						setErrors((e) => {
							const next = { ...e };
							for (const k of Object.keys(patch)) delete next[k as keyof AppearanceForm];
							return next;
						});
					}}
				/>
				<MockBubble value={value} />
			</div>
			{owner ? null : (
				<div className="flex items-center gap-2.5 border-t border-border px-4 py-3 text-[13px] text-foreground-2">
					<Lock className="size-4 shrink-0 text-muted-foreground" aria-hidden />
					<span>{t.appearance.memberNote}</span>
				</div>
			)}
			<FormFoot>
				<Button type="button" variant="ghost" disabled={!dirty || !owner} onClick={() => setValue(saved)}>
					{t.appearance.reset}
				</Button>
				<OwnerGate action="widgetKey.manage">
					<Button
						type="button"
						variant="default"
						disabled={owner && (!dirty || update.isPending)}
						onClick={() => void save()}
					>
						{update.isPending ? t.appearance.saving : t.appearance.save}
					</Button>
				</OwnerGate>
			</FormFoot>
		</Panel>
	);
}
