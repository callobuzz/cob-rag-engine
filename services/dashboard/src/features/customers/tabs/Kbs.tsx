import { useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { Plus } from "lucide-react";
import { useState } from "react";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { Button } from "@/components/ui/button";
import { corporaQuery } from "@/features/kbs/api";
import { CreateKbDialog } from "@/features/kbs/CreateKbDialog";
import { KbAvatar } from "@/features/kbs/KbAvatar";
import { Panel } from "@/features/kbs/ui";
import { ManualConnectDialog } from "@/features/whatsapp/ManualConnectDialog"; // TV6
import type { AdminTenant } from "@/lib/api/accounts-types";
import { type Corpus, infoQuery } from "@/lib/api/queries";
import { formatRelative } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { customersStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { kbsStrings } from "@/lib/strings/kbs";
import { whatsappStrings as wa } from "@/lib/strings/whatsapp"; // TV6
import { kbsText } from "../CustomerRow";

/** Knowledge bases of this customer (`GET /corpora?tenant_id=`) → `/kbs/{id}`. */
export function KbsTab({ tenant }: { tenant: AdminTenant }) {
	const navigate = useNavigate();
	const list = useQuery(corporaQuery({ tenant_id: tenant.id, limit: 500 }));
	const [creating, setCreating] = useState(false);
	const [waKb, setWaKb] = useState<Corpus | null>(null); // TV6
	const waOn = useQuery(infoQuery).data?.features.whatsapp?.enabled === true; // TV6
	const c = t.detail.kbsCols;
	const columns: Column<Corpus>[] = [
		{
			id: "name",
			header: c.name,
			rowHeader: true,
			sortValue: (k) => k.name,
			cell: (k) => (
				<div className="flex min-w-0 items-center gap-2.5">
					<KbAvatar kb={k} />
					<Link
						to="/kbs/$kbId"
						params={{ kbId: k.id }}
						className="truncate font-semibold text-foreground no-underline hover:underline"
					>
						{k.name}
					</Link>
				</div>
			),
		},
		{
			id: "documents",
			header: c.documents,
			align: "right",
			stackLabel: true,
			sortValue: (k) => k.document_count,
			cell: (k) => formatNumber(k.document_count),
		},
		{
			id: "passages",
			header: c.passages,
			align: "right",
			stackLabel: true,
			sortValue: (k) => k.chunk_count,
			cell: (k) => formatNumber(k.chunk_count),
		},
		{
			id: "activity",
			header: c.activity,
			stackLabel: true,
			sortValue: (k) => k.last_activity_at ?? "",
			cell: (k) => (k.last_activity_at ? formatRelative(k.last_activity_at) : t.detail.never),
		},
		// TV6: the deployer connects a number by hand, for this business
		...(waOn
			? [
					{
						id: "whatsapp",
						header: wa.admin.connectTitle,
						stackLabel: true,
						cell: (k: Corpus) => (
							<Button
								size="sm"
								variant="outline"
								aria-label={`${wa.admin.connectButton}: ${k.name}`}
								onClick={(e) => {
									e.stopPropagation();
									setWaKb(k);
								}}
							>
								{wa.admin.connectButton}
							</Button>
						),
					} satisfies Column<Corpus>,
				]
			: []),
		// /TV6
	];
	return (
		<Panel title={fill(t.detail.kbsTitle, { name: tenant.name })} sub={kbsText(tenant)}>
			{list.isPending ? (
				<TableSkeleton rows={2} />
			) : list.isError ? (
				<div className="p-4">
					<ErrorState error={list.error} onRetry={() => void list.refetch()} />
				</div>
			) : list.data.items.length === 0 ? (
				<div className="flex flex-wrap items-center justify-between gap-3 px-4 py-3">
					<p className="m-0 text-[13px] text-muted-foreground">{fill(t.detail.kbsEmpty, { name: tenant.name })}</p>
					{tenant.status === "active" ? (
						<Button size="sm" variant="default" onClick={() => setCreating(true)}>
							<Plus aria-hidden />
							{fill(kbsStrings.list.createFor, { customer: tenant.name })}
						</Button>
					) : null}
				</div>
			) : (
				<div className="overflow-x-auto">
					<DataTable
						caption={fill(t.detail.kbsCaption, { name: tenant.name })}
						columns={columns}
						rows={list.data.items}
						rowKey={(k) => k.id}
						onRowClick={(k) => void navigate({ to: "/kbs/$kbId", params: { kbId: k.id } })}
					/>
				</div>
			)}
			<CreateKbDialog open={creating} onOpenChange={setCreating} defaultTenantId={tenant.id} />
			{waKb ? (
				<ManualConnectDialog
					kb={waKb.id}
					kbName={waKb.name}
					open
					onOpenChange={(o) => {
						if (!o) setWaKb(null);
					}}
				/>
			) : null}
		</Panel>
	);
}
