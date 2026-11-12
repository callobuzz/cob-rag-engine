import { FormAlert } from "@/components/auth/FormAlert";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import type { AdminTenant } from "@/lib/api/accounts-types";
import { operatorStrings, customersStrings as t } from "@/lib/strings/accounts";
import { fill } from "@/lib/strings/en";
import type { LifecycleAction } from "../LifecycleDialogs";

function Row({
	title,
	body,
	label,
	destructive,
	onClick,
}: {
	title: string;
	body: string;
	label: string;
	destructive?: boolean;
	onClick: () => void;
}) {
	return (
		<div className="flex flex-wrap items-center justify-between gap-4 border-b border-border p-4 last:border-b-0">
			<div>
				<b>{title}</b>
				<p className="m-0 mt-0.5 max-w-[560px] text-[13px] text-foreground-2">{body}</p>
			</div>
			<Button variant={destructive ? "destructive" : "outline"} onClick={onClick}>
				{label}
			</Button>
		</div>
	);
}

/** Lifecycle by status (activate/decline, suspend, reactivate) and delete (typed name). */
export function DangerTab({
	tenant,
	onAction,
}: {
	tenant: AdminTenant;
	onAction: (a: LifecycleAction, t: AdminTenant) => void;
}) {
	const a = t.actions;
	const d = t.detail;
	return (
		<Panel title={d.dangerTitle} tone="danger">
			{tenant.is_operator ? (
				<div className="border-b border-border p-4">
					<FormAlert tone="info">
						<b className="font-semibold">{operatorStrings.protectedTitle}.</b> {operatorStrings.protectedBody}
					</FormAlert>
				</div>
			) : tenant.status === "pending" ? (
				<>
					<Row
						title={d.activateRow}
						body={d.activateRowBody}
						label={a.activate}
						onClick={() => onAction("activate", tenant)}
					/>
					<Row
						title={d.declineRow}
						body={d.declineRowBody}
						label={a.decline}
						onClick={() => onAction("decline", tenant)}
					/>
				</>
			) : tenant.status === "active" ? (
				<Row
					title={d.suspendRow}
					body={d.suspendRowBody}
					label={a.suspend}
					onClick={() => onAction("suspend", tenant)}
				/>
			) : (
				<Row
					title={d.reactivateRow}
					body={d.reactivateRowBody}
					label={a.reactivate}
					onClick={() => onAction("reactivate", tenant)}
				/>
			)}
			<Row
				title={d.deleteRow}
				body={fill(tenant.is_operator ? operatorStrings.deleteRowBody : d.deleteRowBody, { name: tenant.name })}
				label={t.remove.confirm}
				destructive
				onClick={() => onAction("delete", tenant)}
			/>
		</Panel>
	);
}
