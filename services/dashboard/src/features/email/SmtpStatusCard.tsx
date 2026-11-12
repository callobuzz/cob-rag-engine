import { CircleCheck, CircleX, ScrollText } from "lucide-react";
import { FormAlert } from "@/components/auth/FormAlert";
import { KeyValue } from "@/components/patterns/KeyValue";
import { Badge } from "@/components/ui/badge";
import { Panel } from "@/features/kbs/ui";
import { emailStrings } from "@/lib/strings/admin";
import { fill } from "@/lib/strings/en";
import type { SmtpStatus } from "./api";

const t = emailStrings.status;

export function ProviderBadge({ provider }: { provider: SmtpStatus["provider"] }) {
	const Icon = provider === "smtp" ? CircleCheck : provider === "log" ? ScrollText : CircleX;
	return (
		<Badge variant={provider === "smtp" ? "ok" : provider === "log" ? "warn" : "bad"} data-testid="smtp-provider">
			<Icon aria-hidden />
			{t.provider[provider]}
		</Badge>
	);
}

/** The dashboard origin differs from the one e-mail links use (RAG_DASHBOARD_URL). */
export function otherOrigin(dashboardUrl: string, origin: string = window.location.origin): boolean {
	try {
		return new URL(dashboardUrl).origin !== origin;
	} catch {
		return true;
	}
}

/** Alerts above the cards: log provider (development) or no provider at all. */
export function SmtpAlerts({ status }: { status: SmtpStatus }) {
	if (status.provider === "log") return <FormAlert tone="warn">{t.logAlert}</FormAlert>;
	if (status.provider === "disabled") return <FormAlert>{t.disabledAlert}</FormAlert>;
	return null;
}

/** Mockup "Mail server": the RAG_SMTP_* values (secrets only as set / not set). */
export function SmtpStatusCard({ status }: { status: SmtpStatus }) {
	const set = (v: boolean, label: string) => (v ? label : t.notSet);
	const other = otherOrigin(status.dashboard_url);
	return (
		<Panel title={t.title} sub={t.sub} aside={<ProviderBadge provider={status.provider} />}>
			<KeyValue
				rows={[
					{ label: "RAG_SMTP_HOST", value: status.host ?? t.notSet, muted: !status.host },
					{ label: "RAG_SMTP_PORT", value: String(status.port), muted: !status.host },
					{ label: "RAG_SMTP_SECURITY", value: status.security, muted: !status.host },
					{
						label: "RAG_SMTP_USERNAME",
						value: set(status.username_set, t.usernameSet),
						muted: !status.username_set,
					},
					{
						label: "RAG_SMTP_PASSWORD",
						value: set(status.password_set, t.passwordSet),
						muted: !status.password_set,
					},
					{ label: "RAG_SMTP_FROM", value: status.from_address ?? t.notSet, muted: !status.from_address },
					{ label: "RAG_DASHBOARD_URL", value: status.dashboard_url },
				]}
			/>
			{other ? (
				<div className="border-t border-border p-3">
					<FormAlert tone="warn">
						{fill(t.otherOrigin, { url: status.dashboard_url, origin: window.location.origin })}
					</FormAlert>
				</div>
			) : null}
		</Panel>
	);
}
