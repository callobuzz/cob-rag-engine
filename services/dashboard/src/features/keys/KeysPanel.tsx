import { useQuery } from "@tanstack/react-query";
import { KeyRound, Lock, Plus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { type Column, DataTable } from "@/components/patterns/DataTable";
import { ErrorState } from "@/components/patterns/ErrorState";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { TableSkeleton } from "@/components/patterns/Skeletons";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { api, unwrap } from "@/lib/api/client";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { qk } from "@/lib/api/query-keys";
import { signOut } from "@/lib/auth/actions";
import { usePrincipal } from "@/lib/auth/store";
import { formatRelative } from "@/lib/format/date";
import { can } from "@/lib/permissions";
import { fill } from "@/lib/strings/en";
import { keysStrings as t } from "@/lib/strings/keys";
import { type ApiKey, type KeyOwner, keysQuery, usedRecently, useRevokeKey, useRotateKey } from "./api";
import { CreateKeyDialog, EditKeyDialog, RevealKeyDialog } from "./KeyDialogs";
import { PermissionBadge } from "./PermissionPicker";

function subFor(owner: KeyOwner): string {
	switch (owner.kind) {
		case "kb":
			return fill(t.sub.kb, { name: owner.name });
		case "customer":
			return fill(t.sub.customer, { name: owner.name });
		case "workspace":
			return t.sub.workspace;
	}
}

/**
 * The keys of a knowledge base, a customer or the caller's workspace: name, prefix, permission,
 * last used and who made each, with create, edit, rotate and revoke. Rotating or revoking a key
 * that was used in the last 24 hours says so first (something live is probably using it). Owners
 * only (`key.manage`): a member sees the note and locked buttons, and the engine refuses a member's
 * request too.
 */
export function KeysPanel({ owner }: { owner: KeyOwner }) {
	const principal = usePrincipal();
	const manage = can(principal, "key.manage");
	// The engine refuses a member the key routes (owner_required): don't ask, say so.
	const q = useQuery({ ...keysQuery(owner), enabled: manage });
	// Signed in with a tk_ key? Then one of these rows is the key holding this session.
	const keyedIn = principal?.auth === "api_key" && principal.keyKind === "tenant" && owner.kind !== "kb";
	const who = useQuery({
		queryKey: qk.whoami,
		queryFn: () => unwrap(api().GET("/api/v1/whoami")),
		enabled: keyedIn,
	});
	const selfId = keyedIn ? (who.data?.key_id ?? null) : null;

	const [creating, setCreating] = useState(false);
	const [editing, setEditing] = useState<ApiKey | null>(null);
	const [rotating, setRotating] = useState<ApiKey | null>(null);
	const [revoking, setRevoking] = useState<ApiKey | null>(null);
	const [reveal, setReveal] = useState<{ title: string; key: string; warning?: string; selfSignOut: boolean } | null>(
		null,
	);
	const [error, setError] = useState<string | null>(null);
	const rotate = useRotateKey(owner);
	const revoke = useRevokeKey(owner);

	const doRotate = async (row: ApiKey) => {
		setError(null);
		try {
			const r = await rotate.mutateAsync(row.id);
			setRotating(null);
			toast.success(t.rotate.toast, { description: row.name });
			setReveal({
				title: fill(t.rotate.revealTitle, { name: row.name }),
				key: r.api_key,
				warning: t.rotate.revealWarn,
				selfSignOut: row.id === selfId,
			});
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	const doRevoke = async (row: ApiKey) => {
		setError(null);
		try {
			await revoke.mutateAsync(row.id);
			setRevoking(null);
			toast.success(t.revoke.toast, { description: row.name });
			if (row.id === selfId) void signOut(null, { callServer: false });
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	const closeReveal = () => {
		const out = reveal?.selfSignOut ?? false;
		setReveal(null);
		if (out) void signOut(null, { callServer: false });
	};

	const columns: Column<ApiKey>[] = [
		{
			id: "name",
			header: t.columns.name,
			rowHeader: true,
			sortValue: (k) => k.name,
			cell: (k) => (
				<span className="flex flex-wrap items-center gap-1.5">
					<b className="font-semibold">{k.name}</b>
					{k.is_default ? <Badge>{t.defaultTag}</Badge> : null}
					{k.id === selfId ? <Badge variant="info">{t.inUseTag}</Badge> : null}
				</span>
			),
		},
		{
			id: "key",
			header: t.columns.key,
			stackLabel: true,
			cell: (k) => <code className="font-mono text-[12.5px] break-all">{k.key_prefix}</code>,
		},
		{
			id: "permission",
			header: t.columns.permission,
			stackLabel: true,
			sortValue: (k) => k.permission,
			cell: (k) => <PermissionBadge permission={k.permission} />,
		},
		{
			id: "used",
			header: t.columns.lastUsed,
			stackLabel: true,
			sortValue: (k) => k.last_used_at ?? "",
			cell: (k) => (k.last_used_at ? formatRelative(k.last_used_at) : t.neverUsed),
		},
		{
			id: "by",
			header: t.columns.createdBy,
			stackLabel: true,
			cell: (k) => (
				<span className="break-all">{k.created_by === "migrated" ? t.createdByMigrated : (k.created_by ?? "—")}</span>
			),
		},
		{
			id: "actions",
			header: t.columns.actions,
			align: "right",
			cell: (k) => (
				<span className="inline-flex flex-wrap justify-end gap-1.5">
					<OwnerGate action="key.manage">
						<Button size="sm" variant="outline" aria-label={`${t.edit.action} ${k.name}`} onClick={() => setEditing(k)}>
							{t.edit.action}
						</Button>
					</OwnerGate>
					<OwnerGate action="key.manage">
						<Button
							size="sm"
							variant="outline"
							aria-label={`${t.rotate.action} ${k.name}`}
							onClick={() => {
								setError(null);
								setRotating(k);
							}}
						>
							{t.rotate.action}
						</Button>
					</OwnerGate>
					<OwnerGate action="key.manage">
						<Button
							size="sm"
							variant="outline"
							aria-label={`${t.revoke.action} ${k.name}`}
							onClick={() => {
								setError(null);
								setRevoking(k);
							}}
						>
							{t.revoke.action}
						</Button>
					</OwnerGate>
				</span>
			),
		},
	];

	const live = (row: ApiKey) => usedRecently(row);
	const when = (row: ApiKey) => formatRelative(row.last_used_at);

	return (
		<Panel
			title={t.title}
			sub={subFor(owner)}
			aside={
				<OwnerGate action="key.manage">
					<Button variant="default" onClick={() => setCreating(true)}>
						<Plus aria-hidden />
						{t.create.button}
					</Button>
				</OwnerGate>
			}
		>
			{!manage ? null : q.isPending ? (
				<TableSkeleton rows={3} />
			) : q.isError ? (
				<div className="p-4">
					<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
				</div>
			) : q.data.length === 0 ? (
				<div className="grid justify-items-start gap-1 p-4">
					<b className="inline-flex items-center gap-2">
						<KeyRound className="size-4 text-muted-foreground" aria-hidden />
						{t.empty.title}
					</b>
					<p className="m-0 max-w-[560px] text-[13px] text-foreground-2">{fill(t.empty.body, { name: owner.name })}</p>
				</div>
			) : (
				<div className="overflow-x-auto">
					<DataTable caption={t.caption} columns={columns} rows={q.data} rowKey={(k) => k.id} />
				</div>
			)}
			{!manage ? (
				<div className="flex items-center gap-2.5 border-t border-border px-4 py-3 text-[13px] text-foreground-2">
					<Lock className="size-4 shrink-0 text-muted-foreground" aria-hidden />
					<span>{t.memberNote}</span>
				</div>
			) : null}

			<CreateKeyDialog
				owner={owner}
				open={creating}
				onOpenChange={setCreating}
				onCreated={(k) => {
					setCreating(false);
					setReveal({ title: fill(t.reveal.title, { name: k.name }), key: k.api_key, selfSignOut: false });
				}}
			/>
			<EditKeyDialog owner={owner} keyRow={editing} onOpenChange={(o) => (o ? undefined : setEditing(null))} />
			<ConfirmDialog
				open={rotating !== null}
				onOpenChange={(o) => (o ? undefined : setRotating(null))}
				title={fill(t.rotate.title, { name: rotating?.name ?? "" })}
				body={t.rotate.body}
				confirmLabel={t.rotate.confirm}
				pending={rotate.isPending}
				onConfirm={() => rotating && void doRotate(rotating)}
			>
				{rotating && live(rotating) ? (
					<FormAlert tone="warn">
						<span data-testid="rotate-live-warning">{fill(t.rotate.liveWarn, { when: when(rotating) })}</span>
					</FormAlert>
				) : null}
				{rotating?.id === selfId ? <FormAlert tone="warn">{t.rotate.selfWarn}</FormAlert> : null}
				{error ? <FormAlert>{error}</FormAlert> : null}
			</ConfirmDialog>
			<TypedConfirmDialog
				open={revoking !== null}
				onOpenChange={(o) => (o ? undefined : setRevoking(null))}
				title={fill(t.revoke.title, { name: revoking?.name ?? "" })}
				body={t.revoke.body}
				word={t.revoke.word}
				confirmLabel={t.revoke.confirm}
				pending={revoke.isPending}
				error={error}
				onConfirm={() => revoking && void doRevoke(revoking)}
			>
				{revoking && live(revoking) ? (
					<FormAlert tone="warn">
						<span data-testid="revoke-live-warning">{fill(t.revoke.liveWarn, { when: when(revoking) })}</span>
					</FormAlert>
				) : null}
				{revoking?.id === selfId ? <FormAlert tone="warn">{t.revoke.selfWarn}</FormAlert> : null}
			</TypedConfirmDialog>
			<RevealKeyDialog
				title={reveal?.title ?? null}
				keyValue={reveal?.key ?? null}
				warning={reveal?.warning}
				selfSignOut={reveal?.selfSignOut}
				onClose={closeReveal}
			/>
		</Panel>
	);
}
