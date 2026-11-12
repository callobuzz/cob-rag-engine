import { useId } from "react";
import { toast } from "sonner";
import { NativeSelect } from "@/components/patterns/NativeSelect";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { can } from "@/lib/permissions";
import { websiteStrings } from "@/lib/strings/website";
import { type RefreshMode, useUpdateWebsite, type WebsiteOut } from "../api";

const t = websiteStrings.refresh;
const MODES: RefreshMode[] = ["hourly", "6h", "daily", "weekly", "monthly", "off"];

/** How often learned pages are checked, and whether busy pages are checked more often. */
export function RefreshSettings({ kb, settings }: { kb: string; settings: WebsiteOut["settings"] }) {
	const update = useUpdateWebsite(kb);
	const principal = usePrincipal();
	const owner = can(principal, "website.manage");
	const selectId = useId();
	const adaptiveId = useId();
	const never = settings.refresh_mode === "off";

	const save = (body: { refresh_mode?: RefreshMode; adaptive?: boolean }) =>
		update.mutate(body, {
			onSuccess: () => toast.success(t.saved),
			onError: (e) => {
				const m = messageFor(toApiError(e));
				toast.error(m.title, m.body ? { description: m.body } : undefined);
			},
		});

	return (
		<Panel title={t.title} sub={t.sub}>
			<div className="grid gap-4 p-4">
				<div className="grid gap-1.5">
					<Label htmlFor={selectId} className="text-[14px] font-semibold">
						{t.label}
					</Label>
					<OwnerGate action="website.manage">
						<NativeSelect
							id={selectId}
							className="max-w-xs"
							value={settings.refresh_mode}
							disabled={update.isPending || !owner}
							onChange={(e) => save({ refresh_mode: e.target.value as RefreshMode })}
						>
							{MODES.map((mode) => (
								<option key={mode} value={mode}>
									{t.options[mode]}
								</option>
							))}
						</NativeSelect>
					</OwnerGate>
					{owner ? null : <span className="text-[12.5px] text-muted-foreground">{t.ownersOnly}</span>}
				</div>
				<div className="flex items-start gap-3">
					<OwnerGate action="website.manage">
						<Checkbox
							id={adaptiveId}
							checked={settings.adaptive && !never}
							disabled={never || update.isPending || !owner}
							onCheckedChange={(v) => save({ adaptive: v === true })}
						/>
					</OwnerGate>
					<div className="grid gap-0.5">
						<Label htmlFor={adaptiveId} className="text-[14px] font-semibold">
							{t.adaptive}
						</Label>
						<span className="text-[12.5px] text-muted-foreground">{never ? t.adaptiveOff : t.adaptiveHint}</span>
					</div>
				</div>
			</div>
		</Panel>
	);
}
