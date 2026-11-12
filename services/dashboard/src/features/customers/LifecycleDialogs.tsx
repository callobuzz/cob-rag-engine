import { type ReactNode, useId, useState } from "react";
import { toast } from "sonner";
import { describedBy, Field } from "@/components/auth/Field";
import { FormAlert } from "@/components/auth/FormAlert";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { TypedConfirmDialog } from "@/components/patterns/TypedConfirmDialog";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import type { AdminTenant } from "@/lib/api/accounts-types";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { operatorStrings, customersStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import { ActivateDialog, NOTE_MAX, useStaleStatus } from "./ActivateDialog";
import { useDeclineTenant, useDeleteTenant, useReactivateTenant, useSuspendTenant } from "./api";
import { ChangePlanDialog } from "./ChangePlanDialog";

export type LifecycleAction = "activate" | "changePlan" | "decline" | "suspend" | "reactivate" | "delete";

/**
 * Every customer lifecycle dialog in one place, shared by the list and the detail page (A4/A6
 * bodies exactly): activate `{plan_id, note}`, change plan `{plan_id}`, decline `{reason}` (plain
 * confirm), suspend `{confirm_name, reason}` (typed name), reactivate (no body, plain confirm),
 * delete `?confirm_name=` (typed name). `confirm_mismatch` shows on the typed field.
 */
export function useCustomerActions(opts: { onGone?: (t: AdminTenant) => void } = {}) {
	const [state, setState] = useState<{ action: LifecycleAction; tenant: AdminTenant } | null>(null);
	const close = () => setState(null);
	const of = (a: LifecycleAction) => (state?.action === a ? state.tenant : null);
	const dialogs: ReactNode = (
		<>
			<ActivateDialog tenant={of("activate")} onOpenChange={(o) => (o ? undefined : close())} />
			<ChangePlanDialog tenant={of("changePlan")} onOpenChange={(o) => (o ? undefined : close())} />
			<DeclineDialog tenant={of("decline")} onClose={close} onGone={opts.onGone} />
			<SuspendDialog tenant={of("suspend")} onClose={close} />
			<ReactivateDialog tenant={of("reactivate")} onClose={close} />
			<DeleteDialog tenant={of("delete")} onClose={close} onGone={opts.onGone} />
		</>
	);
	return { run: (action: LifecycleAction, tenant: AdminTenant) => setState({ action, tenant }), dialogs };
}

function ownerOf(tenant: AdminTenant): string {
	return tenant.owner_emails[0] ?? t.activate.ownerFallback.toLowerCase();
}

function ReasonField({
	value,
	onChange,
	label,
	hint,
	multiline = false,
}: {
	value: string;
	onChange: (v: string) => void;
	label: string;
	hint: string;
	multiline?: boolean;
}) {
	const id = useId();
	const tooLong = value.length > NOTE_MAX;
	const common = {
		id,
		value,
		autoComplete: "off",
		"aria-invalid": tooLong || undefined,
		"aria-describedby": describedBy(id, { hint: true, error: tooLong }),
	};
	return (
		<Field id={id} label={label} hint={hint} error={tooLong ? t.activate.noteLong : null}>
			{multiline ? (
				<Textarea {...common} rows={2} onChange={(e) => onChange(e.target.value)} />
			) : (
				<Input {...common} onChange={(e) => onChange(e.target.value)} />
			)}
		</Field>
	);
}

function useErrorText() {
	const stale = useStaleStatus();
	return (e: unknown) => {
		const err = toApiError(e);
		stale(err);
		return err;
	};
}

function DeclineDialog({
	tenant,
	onClose,
	onGone,
}: {
	tenant: AdminTenant | null;
	onClose: () => void;
	onGone?: (t: AdminTenant) => void;
}) {
	const decline = useDeclineTenant();
	const toErr = useErrorText();
	const [reason, setReason] = useState("");
	const [error, setError] = useState<string | null>(null);
	const reset = () => {
		setReason("");
		setError(null);
		onClose();
	};
	const go = async () => {
		if (!tenant || reason.length > NOTE_MAX) return;
		try {
			await decline.mutateAsync({ id: tenant.id, reason: reason.trim() || null });
			toast.success(t.decline.toast, { description: fill(t.decline.toastBody, { name: tenant.name }) });
			reset();
			onGone?.(tenant);
		} catch (e) {
			setError(messageFor(toErr(e)).title);
		}
	};
	return (
		<ConfirmDialog
			open={tenant !== null}
			onOpenChange={(o) => (o ? undefined : reset())}
			title={fill(t.decline.title, { name: tenant?.name ?? "" })}
			body={tenant ? fill(t.decline.body, { owner: ownerOf(tenant) }) : ""}
			confirmLabel={t.decline.confirm}
			pending={decline.isPending}
			onConfirm={() => void go()}
		>
			<ReasonField value={reason} onChange={setReason} label={t.decline.reason} hint={t.decline.reasonHint} multiline />
			{error ? <FormAlert>{error}</FormAlert> : null}
		</ConfirmDialog>
	);
}

function SuspendDialog({ tenant, onClose }: { tenant: AdminTenant | null; onClose: () => void }) {
	const suspend = useSuspendTenant();
	const toErr = useErrorText();
	const [reason, setReason] = useState("");
	const [fieldError, setFieldError] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	const reset = () => {
		setReason("");
		setFieldError(null);
		setError(null);
		onClose();
	};
	const go = async (typed: string) => {
		if (!tenant || reason.length > NOTE_MAX) return;
		setFieldError(null);
		setError(null);
		try {
			await suspend.mutateAsync({ id: tenant.id, confirm_name: typed, reason: reason.trim() || null });
			toast.success(fill(t.suspend.toast, { name: tenant.name }), { description: t.suspend.toastBody });
			reset();
		} catch (e) {
			const err = toErr(e);
			if (err.code === "confirm_mismatch") setFieldError(messageFor(err).title);
			else setError(messageFor(err).title);
		}
	};
	return (
		<TypedConfirmDialog
			open={tenant !== null}
			onOpenChange={(o) => (o ? undefined : reset())}
			title={fill(t.suspend.title, { name: tenant?.name ?? "" })}
			body={fill(t.suspend.body, { name: tenant?.name ?? "" })}
			word={tenant?.name ?? ""}
			confirmLabel={t.suspend.confirm}
			pending={suspend.isPending}
			error={fieldError}
			onConfirm={(typed) => void go(typed)}
		>
			<ReasonField value={reason} onChange={setReason} label={t.suspend.reason} hint={t.suspend.reasonHint} />
			{error ? <FormAlert>{error}</FormAlert> : null}
		</TypedConfirmDialog>
	);
}

function ReactivateDialog({ tenant, onClose }: { tenant: AdminTenant | null; onClose: () => void }) {
	const reactivate = useReactivateTenant();
	const toErr = useErrorText();
	const [error, setError] = useState<string | null>(null);
	const reset = () => {
		setError(null);
		onClose();
	};
	const go = async () => {
		if (!tenant) return;
		try {
			await reactivate.mutateAsync(tenant.id);
			toast.success(fill(t.reactivate.toast, { name: tenant.name }), { description: t.reactivate.toastBody });
			reset();
		} catch (e) {
			setError(messageFor(toErr(e)).title);
		}
	};
	return (
		<ConfirmDialog
			open={tenant !== null}
			onOpenChange={(o) => (o ? undefined : reset())}
			title={fill(t.reactivate.title, { name: tenant?.name ?? "" })}
			body={t.reactivate.body}
			confirmLabel={t.reactivate.confirm}
			destructive={false}
			pending={reactivate.isPending}
			onConfirm={() => void go()}
		>
			{error ? <FormAlert>{error}</FormAlert> : null}
		</ConfirmDialog>
	);
}

function DeleteDialog({
	tenant,
	onClose,
	onGone,
}: {
	tenant: AdminTenant | null;
	onClose: () => void;
	onGone?: (t: AdminTenant) => void;
}) {
	const del = useDeleteTenant();
	const toErr = useErrorText();
	const ackId = useId();
	const [fieldError, setFieldError] = useState<string | null>(null);
	const [error, setError] = useState<string | null>(null);
	// Only the operator's own workspace asks for this second, explicit confirmation.
	const [ack, setAck] = useState(false);
	const isOperator = tenant?.is_operator === true;
	const reset = () => {
		setFieldError(null);
		setError(null);
		setAck(false);
		onClose();
	};
	const go = async (typed: string) => {
		if (!tenant || (isOperator && !ack)) return;
		setFieldError(null);
		setError(null);
		try {
			await del.mutateAsync({
				id: tenant.id,
				confirm_name: typed,
				...(isOperator ? { confirm_operator_workspace: true } : {}),
			});
			toast.success(fill(t.remove.toast, { name: tenant.name }), { description: t.remove.toastBody });
			reset();
			onGone?.(tenant);
		} catch (e) {
			const err = toErr(e);
			if (err.code === "confirm_mismatch") setFieldError(messageFor(err).title);
			else setError(messageFor(err).title);
		}
	};
	return (
		<TypedConfirmDialog
			open={tenant !== null}
			onOpenChange={(o) => (o ? undefined : reset())}
			title={fill(t.remove.title, { name: tenant?.name ?? "" })}
			body={isOperator ? operatorStrings.deleteBody : t.remove.body}
			word={tenant?.name ?? ""}
			confirmLabel={t.remove.confirm}
			pending={del.isPending}
			blocked={isOperator && !ack}
			error={fieldError}
			onConfirm={(typed) => void go(typed)}
		>
			{isOperator ? (
				<div className="flex items-start gap-2.5 rounded-md border border-border bg-secondary p-3">
					<Checkbox id={ackId} checked={ack} onCheckedChange={(v) => setAck(v === true)} className="mt-0.5" />
					<label htmlFor={ackId} className="text-[13px] leading-snug font-medium">
						{operatorStrings.deleteAck}
					</label>
				</div>
			) : null}
			{error ? <FormAlert>{error}</FormAlert> : null}
		</TypedConfirmDialog>
	);
}
