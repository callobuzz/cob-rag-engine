import { useId, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { Switch } from "@/components/ui/switch";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { brandingStrings } from "@/lib/strings/admin";
import { useSaveSettings } from "./api";

const s = brandingStrings.disclosure;

type Field = "require_ai_disclosure" | "disclosure_wording_editable";

/**
 * TV5: telling visitors it is an AI. Two switches, each saved at once as
 * `{trust: {<field>}}`: whether the badge and first line are always shown (on by default), and
 * whether a business may word the first line itself.
 */
export function DisclosureCard({ require, editable }: { require: boolean; editable: boolean }) {
	const save = useSaveSettings();
	const [error, setError] = useState<string | null>(null);
	const toggle = async (field: Field, next: boolean) => {
		setError(null);
		try {
			await save.mutateAsync({ trust: { [field]: next } });
			const saved =
				field === "require_ai_disclosure"
					? next
						? s.requireSavedOn
						: s.requireSavedOff
					: next
						? s.editableSavedOn
						: s.editableSavedOff;
			toast.success(saved);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	return (
		<Panel title={s.title} sub={s.sub}>
			<div className="grid gap-4 p-4">
				<Row
					checked={require}
					disabled={save.isPending}
					label={s.requireLabel}
					help={s.requireHelp}
					onChange={(v) => void toggle("require_ai_disclosure", v)}
				/>
				<Row
					checked={editable}
					disabled={save.isPending}
					label={s.editableLabel}
					help={s.editableHelp}
					onChange={(v) => void toggle("disclosure_wording_editable", v)}
				/>
				{error ? <FormAlert>{error}</FormAlert> : null}
			</div>
		</Panel>
	);
}

function Row({
	checked,
	disabled,
	label,
	help,
	onChange,
}: {
	checked: boolean;
	disabled: boolean;
	label: string;
	help: string;
	onChange: (value: boolean) => void;
}) {
	const id = useId();
	return (
		<div className="grid gap-1">
			<div className="flex items-center gap-2.5">
				<Switch
					id={id}
					checked={checked}
					disabled={disabled}
					aria-describedby={`${id}-hint`}
					onCheckedChange={onChange}
				/>
				<label htmlFor={id} className="text-[13.5px] font-semibold">
					{label}
				</label>
			</div>
			<small id={`${id}-hint`} className="pl-[42px] text-[12.5px] text-muted-foreground">
				{help}
			</small>
		</div>
	);
}
