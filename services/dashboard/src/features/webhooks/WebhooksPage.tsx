import { useQuery } from "@tanstack/react-query";
import { Plus, Send, Trash2, Zap } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate } from "@/lib/format/date";
import { webhooksStrings as t } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { useDeleteWebhook, useTestWebhook, useToggleWebhook, type Webhook, webhooksQuery } from "./api";
import { WebhookDialog } from "./WebhookDialog";

/** `/admin/webhooks`: the engine's outbound webhooks (no delivery history yet, G-18). */
export function WebhooksPage() {
	const q = useQuery(webhooksQuery);
	const toggle = useToggleWebhook();
	const test = useTestWebhook();
	const del = useDeleteWebhook();
	const [creating, setCreating] = useState(false);
	const [deleting, setDeleting] = useState<Webhook | null>(null);
	const [deleteError, setDeleteError] = useState<string | null>(null);

	const onToggle = async (w: Webhook, enabled: boolean) => {
		try {
			await toggle.mutateAsync({ id: w.id, enabled });
			toast.success(enabled ? t.enabledToast : t.disabledToast, { description: w.url });
		} catch (e) {
			toast.error(messageFor(toApiError(e)).title);
		}
	};

	const onTest = async (w: Webhook) => {
		try {
			await test.mutateAsync(w.id);
			toast.success(t.tested, { description: fill(t.testedBody, { url: w.url }) });
		} catch (e) {
			const err = toApiError(e);
			if (err.status === 502) toast.error(t.testFailed, { description: err.detail || undefined });
			else toast.error(messageFor(err).title);
		}
	};

	const doDelete = async () => {
		if (!deleting) return;
		setDeleteError(null);
		try {
			await del.mutateAsync(deleting.id);
			toast.success(t.deleted, { description: deleting.url });
			setDeleting(null);
		} catch (e) {
			setDeleteError(messageFor(toApiError(e)).title);
		}
	};

	const columns: Column<Webhook>[] = [
		{
			id: "url",
			header: t.cols.url,
			rowHeader: true,
			cell: (w) => <code className="font-mono text-[12.5px] break-all">{w.url}</code>,
		},
		{
			id: "events",
			header: t.cols.events,
			stackLabel: true,
			cell: (w) => (
				<span className="flex flex-wrap gap-1">
					{w.events.map((ev) => (
						<Badge key={ev} variant="info">
							{t.events[ev] ?? ev}
						</Badge>
					))}
				</span>
			),
		},
		{
			id: "enabled",
			header: t.cols.enabled,
			stackLabel: true,
			cell: (w) => (
				<Switch
					checked={w.enabled}
					aria-label={fill(t.enabledLabel, { url: w.url })}
					disabled={toggle.isPending && toggle.variables?.id === w.id}
					onCheckedChange={(on) => void onToggle(w, on)}
				/>
			),
		},
		{ id: "created", header: t.cols.created, stackLabel: true, cell: (w) => formatDate(w.created_at) },
		{
			id: "actions",
			header: t.cols.actions,
			cell: (w) => (
				<span className="flex flex-wrap gap-1.5">
					<Button
						size="sm"
						aria-label={fill(t.testLabel, { url: w.url })}
						disabled={test.isPending && test.variables === w.id}
						onClick={() => void onTest(w)}
					>
						<Send aria-hidden />
						{t.test}
					</Button>
					<Button
						size="sm"
						variant="ghost"
						aria-label={fill(t.deleteLabel, { url: w.url })}
						onClick={() => {
							setDeleteError(null);
							setDeleting(w);
						}}
					>
						<Trash2 aria-hidden />
						{t.delete}
					</Button>
				</span>
			),
		},
	];

	const items = q.data?.webhooks ?? [];
	return (
		<div className="grid gap-4">
			<PageHeader
				title={t.page.title}
				description={t.page.lead}
				actions={
					<Button variant="default" onClick={() => setCreating(true)}>
						<Plus aria-hidden />
						{t.page.add}
					</Button>
				}
			/>
			{q.isPending ? (
				<div className="rounded-lg border border-border bg-card">
					<TableSkeleton rows={3} />
				</div>
			) : q.isError ? (
				<ErrorState error={q.error} title={t.page.error} onRetry={() => void q.refetch()} />
			) : items.length === 0 ? (
				<EmptyState
					title={t.page.emptyTitle}
					body={t.page.emptyBody}
					primaryAction={
						<Button variant="default" onClick={() => setCreating(true)}>
							<Zap aria-hidden />
							{t.page.add}
						</Button>
					}
					art={
						<ul className="m-0 grid list-none gap-2.5 p-0">
							{Object.entries(t.events).map(([code, label]) => (
								<li key={code} className="grid gap-0.5 rounded-md border border-border bg-muted px-4 py-3">
									<b className="text-[13.5px]">{label}</b>
									<code className="font-mono text-[11.5px] text-muted-foreground">{code}</code>
								</li>
							))}
						</ul>
					}
				/>
			) : (
				<div className="overflow-x-auto rounded-lg border border-border bg-card">
					<DataTable caption={t.page.caption} columns={columns} rows={items} rowKey={(w) => w.id} />
				</div>
			)}
			<WebhookDialog open={creating} onOpenChange={setCreating} />
			<ConfirmDialog
				open={deleting !== null}
				onOpenChange={(o) => (o ? undefined : setDeleting(null))}
				title={t.deleteTitle}
				body={fill(t.deleteBody, { url: deleting?.url ?? "" })}
				confirmLabel={t.delete}
				pending={del.isPending}
				onConfirm={() => void doDelete()}
			>
				{deleteError ? <FormAlert>{deleteError}</FormAlert> : null}
			</ConfirmDialog>
		</div>
	);
}
