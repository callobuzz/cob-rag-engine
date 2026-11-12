import { assistantStrings as t } from "@/lib/strings/assistant";
import { ChipsControl, SwitchControl } from "../controls";

/** Who is told about a lead or a handoff: e-mail addresses, the webhooks and a daily summary. */
export function NotifyFields({ prefix = "notify" }: { prefix?: string }) {
	return (
		<div className="grid gap-4">
			<ChipsControl
				name={`${prefix}.emails`}
				label={t.notify.emails}
				hint={t.notify.emailsHint}
				max={5}
				maxLength={254}
				addLabel={t.notify.emailsAdd}
			/>
			<SwitchControl name={`${prefix}.webhook`} label={t.notify.webhook} />
			<SwitchControl name={`${prefix}.daily_digest`} label={t.notify.digest} />
		</div>
	);
}
