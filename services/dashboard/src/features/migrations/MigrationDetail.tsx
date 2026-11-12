import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { ErrorState } from "@/components/patterns/ErrorState";
import { KeyValue } from "@/components/patterns/KeyValue";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { Button } from "@/components/ui/button";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { formatDate, formatDateTime } from "@/lib/format/date";
import { migrationsStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import { type Migration, type MigrationAction, migrationQuery, useMigrationAction } from "./api";
import { dimsOf, MigrationProgress, MigrationStatusBadge, modelOf, strategyLabel } from "./MigrationRow";

const t = migrationsStrings.detail;

/** Rollback is possible for a completed migration until `prune_after`. */
export function rollbackWindowPassed(m: Pick<Migration, "prune_after">, now: number = Date.now()): boolean {
	return !!m.prune_after && new Date(m.prune_after).getTime() < now;
}

/** The message for a failed action: 410 → the window passed; otherwise the engine's detail. */
export function actionErrorText(e: unknown): string {
	const err = toApiError(e);
	if (err.status === 410) return t.rollbackGone;
	if (err.status === 409 && err.detail) return err.detail;
	return messageFor(err).title;
}

/**
 * One migration (`GET /migrations/{id}`, polled every 5 s until it ends): status, progress,
 * timeline, both models and indexes, the error; admin actions Approve, Cancel (plain confirm),
 * Retry and Roll back (typed `rollback`, disabled with its reason after `prune_after`).
 */
export function MigrationDetail({
	id,
	summary,
	kbName,
	isAdmin,
	onClose,
}: {
	id: string | undefined;
	summary: Migration | null;
	kbName: (corpusId: string) => string;
	isAdmin: boolean;
	onClose: () => void;
}) {
	return (
		<Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
			<SheetContent side="right" className="w-[560px] max-w-[94vw] gap-0 overflow-y-auto p-0">
				{id ? <Body id={id} summary={summary} kbName={kbName} isAdmin={isAdmin} /> : null}
			</SheetContent>
		</Sheet>
	);
}

function Body({
	id,
	summary,
	kbName,
	isAdmin,
}: {
	id: string;
	summary: Migration | null;
	kbName: (corpusId: string) => string;
	isAdmin: boolean;
}) {
	const q = useQuery(migrationQuery(id));
	const m = q.data ?? (summary?.id === id ? summary : null);
	const action = useMigrationAction();
	const [confirm, setConfirm] = useState<"cancel" | "rollback" | null>(null);
	const [error, setError] = useState<string | null>(null);
	const kb = m ? kbName(m.corpus_id) : "";

	const run = async (a: MigrationAction) => {
		if (!m) return;
		setError(null);
		try {
			await action.mutateAsync({ id: m.id, action: a });
			setConfirm(null);
			if (a === "approve") toast.success(t.approvedToast, { description: fill(t.approvedBody, { kb }) });
			else if (a === "cancel") toast.success(t.cancelled);
			else if (a === "retry") toast.success(t.retried);
			else toast.success(t.rolledBack);
		} catch (e) {
			setConfirm(null);
			setError(actionErrorText(e));
			void q.refetch();
		}
	};

	const passed = m ? rollbackWindowPassed(m) : false;
	const canCancel = m ? ["pending_approval", "queued", "running"].includes(m.status) : false;

	return (
		<>
			<SheetHeader className="border-b border-border p-4">
				<SheetTitle className="pr-8 break-words">{fill(t.title, { kb: kb || "…" })}</SheetTitle>
				<SheetDescription asChild>
					<div>{m ? <MigrationStatusBadge status={m.status} /> : null}</div>
				</SheetDescription>
			</SheetHeader>
			<div className="grid gap-4 p-4">
				{q.isError && !m ? <ErrorState error={q.error} title={t.error} onRetry={() => void q.refetch()} /> : null}
				{!m && q.isPending ? <CardSkeleton lines={5} /> : null}
				{m ? (
					<>
						{error ? <FormAlert>{error}</FormAlert> : null}
						{m.error ? (
							<FormAlert>
								<b className="font-semibold">{t.failure}</b>
								<span className="block break-words">{m.error}</span>
							</FormAlert>
						) : null}
						<MigrationProgress m={m} />
						{isAdmin ? (
							<div className="flex flex-wrap gap-2" data-testid="migration-actions">
								{m.status === "pending_approval" ? (
									<Button variant="default" disabled={action.isPending} onClick={() => void run("approve")}>
										{t.approve}
									</Button>
								) : null}
								{m.status === "failed" ? (
									<Button variant="default" disabled={action.isPending} onClick={() => void run("retry")}>
										{t.retry}
									</Button>
								) : null}
								{m.status === "completed" ? (
									<Button
										variant="destructive"
										disabled={passed || action.isPending}
										onClick={() => setConfirm("rollback")}
									>
										{t.rollback}
									</Button>
								) : null}
								{canCancel ? (
									<Button variant="destructive" disabled={action.isPending} onClick={() => setConfirm("cancel")}>
										{t.cancel}
									</Button>
								) : null}
							</div>
						) : null}
						{isAdmin && m.status === "completed" && passed ? (
							<p className="m-0 text-[12.5px] text-muted-foreground" data-testid="rollback-reason">
								{fill(t.rollbackExpired, { date: formatDate(m.prune_after) })}
							</p>
						) : null}
						<section className="grid gap-2">
							<h3 className="text-[14px] font-bold">{t.timeline}</h3>
							<div className="rounded-md border border-border">
								<KeyValue
									rows={[
										{ label: t.created, value: formatDateTime(m.created_at) },
										{
											label: t.approved,
											value: m.approved_at ? formatDateTime(m.approved_at) : t.notYet,
											muted: !m.approved_at,
										},
										{
											label: t.started,
											value: m.started_at ? formatDateTime(m.started_at) : t.notYet,
											muted: !m.started_at,
										},
										{
											label: t.completed,
											value: m.completed_at ? formatDateTime(m.completed_at) : t.notYet,
											muted: !m.completed_at,
										},
										...(m.prune_after ? [{ label: t.pruneAfter, value: formatDateTime(m.prune_after) }] : []),
									]}
								/>
							</div>
						</section>
						<div className="rounded-md border border-border">
							<KeyValue
								rows={[
									{
										label: t.from,
										value: fill(t.model, { model: modelOf(m.from_config), dims: dimsOf(m.from_config) }),
									},
									{ label: t.to, value: fill(t.model, { model: modelOf(m.to_config), dims: dimsOf(m.to_config) }) },
									{ label: migrationsStrings.cols.strategy, value: strategyLabel(m.strategy) },
									{ label: t.oldIndex, value: m.old_collection },
									{ label: t.newIndex, value: m.new_collection },
								]}
							/>
						</div>
					</>
				) : null}
			</div>
			<ConfirmDialog
				open={confirm === "cancel"}
				onOpenChange={(o) => (o ? undefined : setConfirm(null))}
				title={t.cancelTitle}
				body={t.cancelBody}
				confirmLabel={t.cancelConfirm}
				pending={action.isPending}
				onConfirm={() => void run("cancel")}
			/>
			<TypedConfirmDialog
				open={confirm === "rollback"}
				onOpenChange={(o) => (o ? undefined : setConfirm(null))}
				title={t.rollbackTitle}
				body={fill(t.rollbackBody, { model: m ? modelOf(m.from_config) : "" })}
				word="rollback"
				confirmLabel={t.rollback}
				pending={action.isPending}
				onConfirm={() => void run("rollback")}
			/>
		</>
	);
}
