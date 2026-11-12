import { useId, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { Switch } from "@/components/ui/switch";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { brandingStrings } from "@/lib/strings/admin";
import { useSaveSettings } from "./api";

const t = brandingStrings.hidePowered;

/** Hide the "Powered by" line (any operator). Saves at once and sends only `{branding: {hide_powered_by}}`. */
export function HidePoweredByCard({ hidden }: { hidden: boolean }) {
	const id = useId();
	const save = useSaveSettings();
	const [error, setError] = useState<string | null>(null);
	const toggle = async (next: boolean) => {
		setError(null);
		try {
			await save.mutateAsync({ branding: { hide_powered_by: next } });
			toast.success(next ? t.saved : t.shown);
		} catch (e) {
			setError(messageFor(toApiError(e), { admin: true }).title);
		}
	};
	return (
		<Panel title={t.title} sub={t.sub}>
			<div className="grid gap-1 p-4">
				<div className="flex items-center gap-2.5">
					<Switch
						id={id}
						checked={hidden}
						disabled={save.isPending}
						aria-describedby={`${id}-hint`}
						onCheckedChange={(v) => void toggle(v)}
					/>
					<label htmlFor={id} className="text-[13.5px] font-semibold">
						{t.title}
					</label>
				</div>
				<small id={`${id}-hint`} className="pl-[42px] text-[12.5px] text-muted-foreground">
					{t.sub}
				</small>
				{error ? <FormAlert>{error}</FormAlert> : null}
			</div>
		</Panel>
	);
}
