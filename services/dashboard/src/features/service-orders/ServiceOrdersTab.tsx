import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { type ReactNode, useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { Pager } from "@/components/patterns/Pager";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Textarea } from "@/components/ui/textarea";
import type { ServiceOrderAdmin, ServiceOrderStatus } from "@/lib/api/billing-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { shopStrings } from "@/lib/strings/shop";
import { adminOrdersQuery, NEXT_STATUS, ORDERS_PAGE, useUpdateServiceOrder } from "./api";

const s = shopStrings.orders_admin;
const STATUSES: ServiceOrderStatus[] = ["requested", "in_progress", "done", "cancelled"];
const TONE: Record<ServiceOrderStatus, "default" | "info" | "ok" | "warn"> = {
	requested: "warn",
	in_progress: "info",
	done: "ok",
	cancelled: "default",
};
const statusBadge = (status: ServiceOrderStatus): ReactNode => (
	<Badge variant={TONE[status]}>{shopStrings.orders.status[status]}</Badge>
);

/** Billing → Service orders (P5): what businesses ordered, a status filter, and a drawer to move one along. */
export function ServiceOrdersTab() {
	const filterId = useId();
	const [status, setStatus] = useState<ServiceOrderStatus | "">("");
	const [page, setPage] = useState(1);
	const [openId, setOpenId] = useState<string | null>(null);
	const q = useQuery(adminOrdersQuery({ status: status || undefined, offset: (page - 1) * ORDERS_PAGE }));
	const rows = q.data?.items ?? [];
	const open = rows.find((r) => r.id === openId) ?? null;

	const columns: Column<ServiceOrderAdmin>[] = [
		{ id: "business", header: s.columns.business, rowHeader: true, cell: (r) => r.tenant_name ?? "—" },
		{ id: "service", header: s.columns.service, stackLabel: true, cell: (r) => r.name },
		{ id: "status", header: s.columns.status, stackLabel: true, cell: (r) => statusBadge(r.status) },
		{
			id: "ordered",
			header: s.columns.ordered,
			stackLabel: true,
			sortValue: (r) => r.created_at,
			cell: (r) => formatDate(r.created_at),
		},
		{
			id: "support",
			header: s.columns.support,
			stackLabel: true,
			cell: (r) => (r.support_until ? formatDate(r.support_until) : "—"),
		},
		{
			id: "open",
			header: "",
			cell: (r) => (
				<Button type="button" variant="ghost" size="sm" onClick={() => setOpenId(r.id)}>
					{s.open}
				</Button>
			),
		},
	];

	let body: ReactNode;
	if (q.isPending) body = <TableSkeleton rows={4} />;
	else if (q.isError)
		body = (
			<div className="p-4">
				<ErrorState error={q.error} title={s.loadError} onRetry={() => void q.refetch()} />
			</div>
		);
	else if (rows.length === 0)
		body = (
			<p data-testid="orders-empty" className="m-0 p-6 text-foreground-2">
				{status ? s.emptyFilter : s.empty}
			</p>
		);
	else
		body = (
			<>
				<DataTable caption={s.caption} columns={columns} rows={rows} rowKey={(r) => r.id} />
				{(q.data?.total ?? 0) > ORDERS_PAGE ? (
					<Pager page={page} pageSize={ORDERS_PAGE} total={q.data?.total ?? 0} onPage={setPage} />
				) : null}
			</>
		);

	return (
		<div className="grid gap-4" data-testid="billing-service-orders">
			<div className="flex flex-wrap items-center justify-between gap-3">
				<p className="m-0 max-w-[62ch] text-foreground-2">{s.lead}</p>
				<div>
					<label htmlFor={filterId} className="sr-only">
						{s.filter}
					</label>
					<NativeSelect
						id={filterId}
						value={status}
						onChange={(e) => {
							setStatus(e.target.value as ServiceOrderStatus | "");
							setPage(1);
						}}
					>
						<option value="">{s.allStatuses}</option>
						{STATUSES.map((st) => (
							<option key={st} value={st}>
								{shopStrings.orders.status[st]}
							</option>
						))}
					</NativeSelect>
				</div>
			</div>
			<section className="min-w-0 rounded-lg border border-border bg-card" aria-label={s.caption}>
				{body}
			</section>
			{open ? <OrderSheet key={open.id} order={open} onClose={() => setOpenId(null)} /> : null}
		</div>
	);
}

function OrderSheet({ order, onClose }: { order: ServiceOrderAdmin; onClose: () => void }) {
	const id = useId();
	const update = useUpdateServiceOrder();
	const [pub, setPub] = useState(order.public_note ?? "");
	const [internal, setInternal] = useState(order.internal_note ?? "");
	const [failure, setFailure] = useState<string | null>(null);
	useEffect(() => {
		setPub(order.public_note ?? "");
		setInternal(order.internal_note ?? "");
	}, [order.public_note, order.internal_note]);

	const send = async (body: { status?: ServiceOrderStatus }) => {
		setFailure(null);
		try {
			await update.mutateAsync({
				id: order.id,
				body: {
					...body,
					public_note: pub.trim() === "" ? null : pub.trim(),
					internal_note: internal.trim() === "" ? null : internal.trim(),
				},
			});
			if (body.status) toast.success(fill(s.moved, { status: shopStrings.orders.status[body.status] }));
			else toast.success(s.saved);
		} catch (e) {
			setFailure(messageFor(toApiError(e)).title);
		}
	};
	const moves = NEXT_STATUS[order.status];
	return (
		<Sheet open onOpenChange={(o) => !o && onClose()}>
			<SheetContent className="overflow-y-auto sm:max-w-[480px]" data-testid="order-sheet">
				<SheetHeader>
					<SheetTitle>{fill(s.drawerTitle, { service: order.name, business: order.tenant_name ?? "" })}</SheetTitle>
					<SheetDescription>{fill(s.drawerLead, { date: formatDate(order.created_at) })}</SheetDescription>
				</SheetHeader>
				<div className="grid gap-4 px-4 pb-4">
					<div className="flex flex-wrap items-center gap-2">
						{statusBadge(order.status)}
						{order.support_until ? (
							<small className="text-muted-foreground">
								{fill(shopStrings.orders.supportUntil, { date: formatDate(order.support_until) })}
							</small>
						) : null}
					</div>
					{order.owner_note ? (
						<p className="m-0 rounded-md border border-border bg-secondary px-3 py-2 text-[13px]">
							<b>{s.ownerNote}:</b> {order.owner_note}
						</p>
					) : null}
					{failure ? <FormAlert>{failure}</FormAlert> : null}
					<Field id={`${id}-public`} label={s.publicNote} hint={s.publicNoteHint}>
						<Textarea
							id={`${id}-public`}
							rows={3}
							maxLength={1000}
							value={pub}
							onChange={(e) => setPub(e.target.value)}
						/>
					</Field>
					<Field id={`${id}-internal`} label={s.internalNote} hint={s.internalNoteHint}>
						<Textarea
							id={`${id}-internal`}
							rows={3}
							maxLength={4000}
							value={internal}
							onChange={(e) => setInternal(e.target.value)}
						/>
					</Field>
					<div>
						<Button type="button" variant="outline" disabled={update.isPending} onClick={() => void send({})}>
							{update.isPending ? s.saving : s.saveNotes}
						</Button>
					</div>
					{moves.length > 0 ? (
						<div className="grid gap-2 border-t border-border pt-4">
							<span className="text-[13px] font-semibold">{s.move}</span>
							<div className="flex flex-wrap gap-2">
								{moves.map((next) => (
									<Button
										key={next}
										type="button"
										variant={next === "cancelled" ? "destructive" : "default"}
										disabled={update.isPending}
										onClick={() => void send({ status: next })}
									>
										{s.moveTo[next as "in_progress" | "done" | "cancelled"]}
									</Button>
								))}
							</div>
							{moves.includes("cancelled") ? <small className="text-muted-foreground">{s.cancelNote}</small> : null}
						</div>
					) : (
						<p className="m-0 border-t border-border pt-4 text-[13px] text-muted-foreground">
							{fill(s.final, { status: shopStrings.orders.status[order.status].toLowerCase() })}
						</p>
					)}
					{order.support_request_id ? (
						<Link
							to="/admin/support"
							search={{ request: order.support_request_id }}
							className="w-fit text-[13px] font-medium text-primary hover:underline"
						>
							{s.supportRequest}
						</Link>
					) : null}
				</div>
			</SheetContent>
		</Sheet>
	);
}
