import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useId, useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { Button } from "@/components/ui/button";
import { usePrincipal } from "@/lib/auth/store";
import { formatNumber } from "@/lib/format/number";
import { tenantLabel } from "@/lib/format/tenant";
import { en, fill } from "@/lib/strings/en";
import { kbsStrings as t } from "@/lib/strings/kbs";
import { activeTenantsQuery, type CorpusDetail, useDeleteCorpus, useUpdateCorpus } from "../api";
import { KbErrorAlert } from "../KbErrorAlert";
import { Panel } from "../ui";
import { toastSaveError } from "./shared";

/** Danger zone (mockup `t-danger`): move (super admin only) and delete (owner, typed name). */
export function DangerTab({ kb }: { kb: CorpusDetail }) {
	const principal = usePrincipal();
	const isAdmin = principal?.view === "admin";
	const del = useDeleteCorpus(kb.id);
	const navigate = useNavigate();
	const [deleting, setDeleting] = useState(false);
	const counts = {
		docs: formatNumber(kb.document_count),
		chunks: formatNumber(kb.chunk_count),
		collection: kb.qdrant_collection,
	};

	const doDelete = async () => {
		try {
			await del.mutateAsync(kb.name);
			setDeleting(false);
			toast.success(fill(t.danger.deletedToast, { name: kb.name }));
			void navigate({ to: "/kbs" });
		} catch (e) {
			setDeleting(false);
			toastSaveError(e);
		}
	};

	return (
		<Panel title={t.detail.tabs.danger} tone="danger">
			{isAdmin ? <MoveRow kb={kb} /> : null}
			<div className="flex flex-wrap items-center justify-between gap-4 p-4">
				<div>
					<b>{t.danger.deleteTitle}</b>
					<p className="m-0 mt-0.5 max-w-[560px] text-[13px] text-foreground-2">{fill(t.danger.deleteBody, counts)}</p>
				</div>
				<OwnerGate action="kb.delete">
					<Button variant="destructive" onClick={() => setDeleting(true)}>
						{fill(t.danger.delete, { name: kb.name })}
					</Button>
				</OwnerGate>
			</div>
			<TypedConfirmDialog
				open={deleting}
				onOpenChange={setDeleting}
				title={fill(t.danger.deleteConfirmTitle, { name: kb.name })}
				body={
					<>
						<p className="m-0 mb-2">This permanently deletes:</p>
						<ul className="m-0 grid list-disc gap-1 pl-5">
							{t.danger.deleteList.map((line) => (
								<li key={line}>{fill(line, counts)}</li>
							))}
						</ul>
					</>
				}
				word={kb.name}
				confirmLabel={fill(t.danger.delete, { name: kb.name })}
				pending={del.isPending}
				onConfirm={() => void doDelete()}
			/>
		</Panel>
	);
}

function MoveRow({ kb }: { kb: CorpusDetail }) {
	const id = useId();
	const tenants = useQuery(activeTenantsQuery);
	const update = useUpdateCorpus(kb.id);
	const [target, setTarget] = useState("");
	const [confirming, setConfirming] = useState(false);
	const [error, setError] = useState<unknown>(null);
	const choices = (tenants.data?.items ?? []).filter((x) => x.id !== kb.tenant_id);
	const targetName = choices.find((x) => x.id === target)?.name ?? "";

	const move = async () => {
		setError(null);
		try {
			await update.mutateAsync({ tenant_id: target });
			setConfirming(false);
			setTarget("");
			toast.success(fill(t.danger.movedToast, { tenant: targetName }));
		} catch (e) {
			setConfirming(false);
			setError(e);
		}
	};

	return (
		<div className="grid gap-3 border-b border-border p-4">
			<div className="flex flex-wrap items-end justify-between gap-4">
				<div>
					<b>{t.danger.moveTitle}</b>
					<p className="m-0 mt-0.5 max-w-[560px] text-[13px] text-foreground-2">{t.danger.moveBody}</p>
				</div>
				<div className="flex flex-wrap items-end gap-2">
					<div className="grid gap-1">
						<label htmlFor={id} className="text-[12.5px] font-semibold">
							{t.danger.moveTo}
						</label>
						<NativeSelect
							id={id}
							className="w-56"
							value={target}
							disabled={tenants.isError || tenants.isPending}
							onChange={(e) => setTarget(e.target.value)}
						>
							<option value="">{t.create.customerPick}</option>
							{choices.map((x) => (
								<option key={x.id} value={x.id}>
									{tenantLabel(x)}
								</option>
							))}
						</NativeSelect>
					</div>
					<Button disabled={!target} onClick={() => setConfirming(true)}>
						{t.danger.move}
					</Button>
					{tenants.isError ? (
						<Button variant="ghost" size="sm" onClick={() => void tenants.refetch()}>
							{t.create.customersError} {en.common.retry}
						</Button>
					) : null}
				</div>
			</div>
			{error ? <KbErrorAlert error={error} hideAction /> : null}
			<ConfirmDialog
				open={confirming}
				onOpenChange={setConfirming}
				title={fill(t.danger.moveConfirmTitle, { name: kb.name, tenant: targetName })}
				body={fill(t.danger.moveConfirmBody, { tenant: targetName, from: kb.tenant_name })}
				confirmLabel={t.danger.move}
				destructive={false}
				pending={update.isPending}
				onConfirm={() => void move()}
			/>
		</div>
	);
}
