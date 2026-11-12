import { useId, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { Switch } from "@/components/ui/switch";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { brandingStrings } from "@/lib/strings/admin";
import { useSaveSettings } from "./api";

const s = brandingStrings.humanButton;

/**
 * TV1: may a business hide the "Talk to a person" button of its widget? Off by default. The switch
 * saves at once and sends only `{trust: {allow_hide_human_button}}`.
 */
export function TrustCard({ allowHideHumanButton }: { allowHideHumanButton: boolean }) {
	const id = useId();
	const save = useSaveSettings();
	const [error, setError] = useState<string | null>(null);
	const toggle = async (next: boolean) => {
		setError(null);
		try {
			await save.mutateAsync({ trust: { allow_hide_human_button: next } });
			toast.success(next ? s.savedOn : s.savedOff);
		} catch (e) {
			setError(messageFor(toApiError(e)).title);
		}
	};
	return (
		<Panel title={s.title} sub={s.sub}>
			<div className="grid gap-1 p-4">
				<div className="flex items-center gap-2.5">
					<Switch
						id={id}
						checked={allowHideHumanButton}
						disabled={save.isPending}
						aria-describedby={`${id}-hint`}
						onCheckedChange={(v) => void toggle(v)}
					/>
					<label htmlFor={id} className="text-[13.5px] font-semibold">
						{s.label}
					</label>
				</div>
				<small id={`${id}-hint`} className="pl-[42px] text-[12.5px] text-muted-foreground">
					{s.help}
				</small>
				{error ? <FormAlert>{error}</FormAlert> : null}
			</div>
		</Panel>
	);
}
