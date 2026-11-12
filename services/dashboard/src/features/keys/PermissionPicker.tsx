import { useId } from "react";
import { Badge } from "@/components/ui/badge";
import { keysStrings as t } from "@/lib/strings/keys";
import { cn } from "@/lib/utils";
import { KEY_PERMISSIONS, type KeyPermission } from "./api";

/** read = green (safe), write = amber, full = red (it can delete and manage keys). */
const TONE = { read: "ok", write: "warn", full: "bad" } as const;

export function PermissionBadge({ permission }: { permission: KeyPermission }) {
	return <Badge variant={TONE[permission]}>{t.permission[permission].label}</Badge>;
}

/**
 * The permission of a key, as three radio options with what each allows spelled out. A native
 * radio group (arrow keys, screen readers), the way a permission should be chosen: deliberately,
 * with the consequence in view.
 */
export function PermissionPicker({
	value,
	onChange,
	legend = t.permission.fieldLabel,
	name,
	invalid = false,
	disabled = false,
}: {
	value: KeyPermission;
	onChange: (v: KeyPermission) => void;
	legend?: string;
	name?: string;
	invalid?: boolean;
	disabled?: boolean;
}) {
	const group = useId();
	return (
		<fieldset className="m-0 grid gap-2 border-0 p-0" disabled={disabled} aria-invalid={invalid || undefined}>
			<legend className="mb-1 p-0 text-[13px] font-semibold">{legend}</legend>
			{KEY_PERMISSIONS.map((p) => {
				const id = `${group}-${p}`;
				const on = value === p;
				return (
					<label
						key={p}
						htmlFor={id}
						className={cn(
							"flex cursor-pointer items-start gap-2.5 rounded-md border p-2.5 text-[13px]",
							on ? "border-primary bg-primary-soft" : "border-border bg-muted",
							disabled && "cursor-not-allowed opacity-60",
						)}
					>
						<input
							id={id}
							type="radio"
							name={name ?? group}
							value={p}
							checked={on}
							onChange={() => onChange(p)}
							className="mt-0.5 size-4 shrink-0 accent-[var(--primary)]"
						/>
						<span className="min-w-0">
							<b className="font-semibold">{t.permission[p].label}</b>
							<span className="block text-[12.5px] text-foreground-2">{t.permission[p].help}</span>
						</span>
					</label>
				);
			})}
		</fieldset>
	);
}

/** Read < write < full, for "lowering a permission" warnings. */
export function permissionRank(p: KeyPermission): number {
	return KEY_PERMISSIONS.indexOf(p);
}
