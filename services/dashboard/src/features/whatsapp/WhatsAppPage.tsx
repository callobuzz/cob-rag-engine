import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Database } from "lucide-react";
import { EmptyState } from "@/components/patterns/EmptyState";
import { ErrorState } from "@/components/patterns/ErrorState";
import { CardSkeleton } from "@/components/patterns/Skeletons";
import { PageHeader } from "@/components/shell/PageHeader";
import { buttonVariants } from "@/components/ui/button";
import { infoQuery } from "@/lib/api/queries";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { useMainKb } from "@/lib/main-kb";
import { fill } from "@/lib/strings/en";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { settingsOf, whatsappQuery } from "./api";
import { ChatsList } from "./ChatsList";
import { ConnectCard } from "./ConnectCard";
import { DocumentsCard } from "./DocumentsCard";
import { SettingsCard } from "./SettingsCard";

/**
 * `/whatsapp` for a business (TV6): connect the number, tune how the assistant behaves on it, choose
 * the files it may send, and read and answer the chats. Shown in the menu only when the engine has
 * WhatsApp set up and the plan includes it (`features.whatsapp.available`); the engine refuses the
 * routes otherwise.
 */
export function WhatsAppPage() {
	const main = useMainKb();
	const header = <PageHeader title={t.title} description={t.lead} />;
	if (main.isLoading) {
		return (
			<div className="grid gap-4" aria-busy="true">
				{header}
				<CardSkeleton lines={3} />
			</div>
		);
	}
	if (main.isError) {
		return (
			<div className="grid gap-4">
				{header}
				<ErrorState error={main.error} title={t.loadError} onRetry={main.refetch} />
			</div>
		);
	}
	if (!main.kbId) {
		return (
			<div className="grid gap-4">
				{header}
				<EmptyState
					title={t.noKbTitle}
					body={t.noKbBody}
					primaryAction={
						<Link to="/kbs" search={{ new: 1 }} className={buttonVariants({ variant: "default" })}>
							<Database aria-hidden />
							{t.noKbAction}
						</Link>
					}
				/>
			</div>
		);
	}
	return <WhatsAppForKb key={main.kbId} kb={main.kbId} />;
}

function WhatsAppForKb({ kb }: { kb: string }) {
	const { productName } = useBranding();
	const info = useQuery(infoQuery);
	const q = useQuery(whatsappQuery(kb));
	const header = <PageHeader title={t.title} description={t.lead} />;
	if (q.isPending) {
		return (
			<div className="grid gap-4" aria-busy="true">
				{header}
				<CardSkeleton lines={3} />
			</div>
		);
	}
	if (q.isError) {
		return (
			<div className="grid gap-4">
				{header}
				<ErrorState error={q.error} title={t.loadError} onRetry={() => void q.refetch()} />
			</div>
		);
	}
	const data = q.data;
	const engineOn = info.data?.features.whatsapp?.enabled !== false;
	if (!data.available && !data.status) {
		return (
			<div className="grid gap-4">
				{header}
				<EmptyState
					title={engineOn ? t.notAvailableTitle : t.notSetUpTitle}
					body={fill(engineOn ? t.notAvailableBody : t.notSetUpBody, { product: productName })}
				/>
				<ChatsList kb={kb} />
			</div>
		);
	}
	return (
		<div className="grid gap-4">
			{header}
			<ConnectCard kb={kb} data={data} />
			{data.status ? (
				<>
					<SettingsCard key={`${data.status}-${settingsOf(data).human_pause_hours}`} kb={kb} data={data} />
					<DocumentsCard kb={kb} data={data} />
				</>
			) : null}
			<ChatsList kb={kb} />
		</div>
	);
}
