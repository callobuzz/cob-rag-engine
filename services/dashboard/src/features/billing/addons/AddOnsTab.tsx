import { useQuery } from "@tanstack/react-query";
import { Pencil, Plus, Power, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { AddOn } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { isApiError, toApiError } from "@/lib/api/errors";
import { type Currency, formatMinor } from "@/lib/format/money";
import { addOnStrings as s } from "@/lib/strings/billing-addons";
import { fill } from "@/lib/strings/en";
import { shopStrings } from "@/lib/strings/shop"; // P5 plans
import { AddOnDialog } from "./AddOnDialog";
import { addOnsQuery, useDeleteAddOn, useUpdateAddOn } from "./api";
import { addOnSummary, KINDS } from "./summary";

/** Billing → Add-ons: the catalogue, grouped by kind, with create, edit, deactivate and delete. */
export function AddOnsTab() {
	const [inactive, setInactive] = useState(false);
	const q = useQuery(addOnsQuery(inactive));
	const [dialog, setDialog] = useState<{ editing: AddOn | null } | null>(null);
	const switchId = useId();

	const header = (
		<div className="flex flex-wrap items-center justify-between gap-3">
			<p className="m-0 max-w-[62ch] text-foreground-2">{s.lead}</p>
			<div className="flex flex-wrap items-center gap-3">
				<label htmlFor={switchId} className="flex items-center gap-2 text-[13px]">
					<input
						id={switchId}
						type="checkbox"
						checked={inactive}
						onChange={(e) => setInactive(e.target.checked)}
						className="size-4 accent-[var(--primary)]"
					/>
					{s.showInactive}
				</label>
				<Button type="button" variant="default" onClick={() => setDialog({ editing: null })}>
					<Plus aria-hidden className="size-4" />
					{s.new}
				</Button>
			</div>
		</div>
	);

	let body: React.ReactNode;
	if (q.isPending) {
		body = (
			<div className="grid gap-4 min-[861px]:grid-cols-2" aria-busy="true">
				<CardSkeleton lines={3} />
				<CardSkeleton lines={3} />
			</div>
		);
	} else if (q.isError) {
		body = <ErrorState error={q.error} title={s.loadError} onRetry={() => void q.refetch()} />;
	} else if (q.data.items.length === 0) {
		body = (
			<p data-testid="add-ons-empty" className="m-0 rounded-lg border border-border bg-card p-6 text-foreground-2">
				{s.empty}
			</p>
		);
	} else {
		const items = q.data.items;
		body = (
			<div className="grid gap-6">
				{KINDS.map((kind) => {
					const group = items.filter((a) => a.kind === kind);
					return (
						<section key={kind} aria-labelledby={`add-on-group-${kind}`} className="grid gap-3">
							<div>
								<h2 id={`add-on-group-${kind}`} className="text-[15px] font-bold">
									{s.groups[kind]}
								</h2>
								<p className="m-0 text-[12.5px] text-muted-foreground">{s.groupLeads[kind]}</p>
							</div>
							{group.length === 0 ? (
								<p className="m-0 text-[13px] text-muted-foreground">{s.emptyKind}</p>
							) : (
								<div className="grid gap-4 min-[861px]:grid-cols-2 min-[1101px]:grid-cols-3">
									{group.map((a) => (
										<AddOnCard key={a.id} addOn={a} onEdit={() => setDialog({ editing: a })} />
									))}
								</div>
							)}
						</section>
					);
				})}
			</div>
		);
	}

	return (
		<div className="grid gap-4" data-testid="billing-add-ons">
			{header}
			{body}
			<AddOnDialog
				open={dialog !== null}
				onOpenChange={(o) => {
					if (!o) setDialog(null);
				}}
				editing={dialog?.editing ?? null}
			/>
		</div>
	);
}

function AddOnCard({ addOn, onEdit }: { addOn: AddOn; onEdit: () => void }) {
	const update = useUpdateAddOn();
	const remove = useDeleteAddOn();
	const [confirming, setConfirming] = useState(false);
	const [failure, setFailure] = useState<string | null>(null);

	const toggle = async () => {
		setFailure(null);
		try {
			await update.mutateAsync({ id: addOn.id, body: { is_active: !addOn.is_active } });
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
		}
	};
	const doDelete = async () => {
		setFailure(null);
		try {
			await remove.mutateAsync(addOn.id);
			toast.success(s.deleted, { description: addOn.name });
			setConfirming(false);
		} catch (e) {
			const err = toApiError(e);
			setConfirming(false);
			setFailure(isApiError(err) && err.code === "add_on_in_use" ? s.inUse : messageFor(err).title);
		}
	};

	return (
		<article
			aria-labelledby={`add-on-${addOn.id}`}
			data-testid="add-on-card"
			className="flex min-w-0 flex-col rounded-lg border border-border bg-card"
		>
			<div className="grid gap-2 border-b border-border p-4">
				<h3 id={`add-on-${addOn.id}`} className="flex flex-wrap items-center gap-2 text-[16px] font-bold">
					<span className="min-w-0 break-words">{addOn.name}</span>
					{addOn.is_active ? null : <Badge variant="outline">{s.inactive}</Badge>}
					{/* P5 plans */}
					{addOn.sold_to_owners ? <Badge variant="info">{shopStrings.admin.summary.shop}</Badge> : null}
					{addOn.placeholder ? (
						<Badge variant="warn" data-testid="placeholder-badge" title={shopStrings.admin.placeholderHint}>
							{shopStrings.admin.placeholderBadge}
						</Badge>
					) : null}
					{/* /P5 plans */}
				</h3>
				<p data-testid="add-on-summary" className="m-0 text-[14px] font-semibold">
					{addOnSummary(addOn)}
				</p>
				<p className="m-0 text-[13px] text-foreground-2">
					{addOn.price_minor === 0
						? s.free
						: fill(s.price, { price: formatMinor(addOn.price_minor, addOn.currency as Currency) })}
				</p>
				{addOn.description ? <p className="m-0 text-[12.5px] text-muted-foreground">{addOn.description}</p> : null}
				{failure ? (
					<p role="alert" className="m-0 text-[12.5px] font-medium text-destructive">
						{failure}
					</p>
				) : null}
			</div>
			<div className="flex flex-wrap justify-end gap-2 px-4 py-3">
				<Button size="sm" variant="ghost" onClick={onEdit}>
					<Pencil aria-hidden />
					{s.edit}
				</Button>
				<Button size="sm" variant="ghost" onClick={() => void toggle()} disabled={update.isPending}>
					<Power aria-hidden />
					{addOn.is_active ? s.deactivate : s.activate}
				</Button>
				<Button size="sm" variant="ghost" onClick={() => setConfirming(true)}>
					<Trash2 aria-hidden />
					{s.delete}
				</Button>
			</div>
			<ConfirmDialog
				open={confirming}
				onOpenChange={setConfirming}
				title={fill(s.deleteTitle, { name: addOn.name })}
				body={s.deleteBody}
				confirmLabel={s.deleteConfirm}
				pending={remove.isPending}
				onConfirm={() => void doDelete()}
			/>
		</article>
	);
}
