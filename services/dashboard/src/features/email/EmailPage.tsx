import { useQuery } from "@tanstack/react-query";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { SettingsGrid } from "@/features/kbs/ui";
import { usePrincipal } from "@/lib/auth/store";
import { emailStrings as t } from "@/lib/strings/admin";
import { smtpQuery } from "./api";
import { SmtpAlerts, SmtpStatusCard } from "./SmtpStatusCard";
import { TemplatesList } from "./TemplatesList";
import { TestEmailForm } from "./TestEmailForm";

/** `/admin/email` (mockup `[data-view=email]`): SMTP status (read-only, from .env) and a test send. */
export function EmailPage() {
	const q = useQuery(smtpQuery);
	const principal = usePrincipal();
	return (
		<>
			<PageHeader title={t.page.title} description={t.page.lead} />
			{q.data ? <SmtpAlerts status={q.data} /> : null}
			<SettingsGrid>
				<div className="grid min-w-0 content-start gap-4">
					{q.isPending ? (
						<CardSkeleton lines={6} />
					) : q.isError ? (
						<ErrorState error={q.error} title={t.status.error} onRetry={() => void q.refetch()} />
					) : (
						<SmtpStatusCard status={q.data} />
					)}
					<TestEmailForm defaultTo={principal?.user?.email ?? ""} />
				</div>
				<TemplatesList />
			</SettingsGrid>
		</>
	);
}
