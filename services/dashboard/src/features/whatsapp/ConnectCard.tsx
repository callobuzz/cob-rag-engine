import { CircleAlert, Unplug } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { ConfirmDialog } from "@/components/patterns/ConfirmDialog";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { StatusBadge } from "@/components/patterns/StatusBadge";
import { Button } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatRelative } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { useCheck, useDisconnect, type WhatsApp } from "./api";
import { EmbeddedSignupButton } from "./EmbeddedSignupButton";

const VARIANT: Record<string, "active" | "pending" | "failed" | "suspended"> = {
	active: "active",
	pending: "pending",
	error: "failed",
	disabled: "suspended",
};

/**
 * The number (TV6): either "Connect your WhatsApp number" (the business signs in with Meta) or the
 * connected number with its status, who pays Meta, a connection check and Disconnect (owners).
 * A number the deployer connected by hand shows the same card; its token is never shown.
 */
export function ConnectCard({ kb, data }: { kb: string; data: WhatsApp }) {
	const { productName } = useBranding();
	if (!data.status) {
		return (
			<Panel title={t.connect.title} sub={t.connect.body}>
				<div className="p-4">
					{data.embedded_signup ? (
						<OwnerGate action="whatsapp.manage">
							<div>
								<EmbeddedSignupButton kb={kb} info={data.embedded_signup} />
							</div>
						</OwnerGate>
					) : (
						<p className="m-0 text-foreground-2" data-testid="wa-no-embedded">
							{fill(t.connect.noEmbedded, { product: productName })}
						</p>
					)}
				</div>
			</Panel>
		);
	}
	return <ConnectedNumber kb={kb} data={data} />;
}

function ConnectedNumber({ kb, data }: { kb: string; data: WhatsApp }) {
	const { productName } = useBranding();
	const check = useCheck(kb);
	const disconnect = useDisconnect(kb);
	const [confirm, setConfirm] = useState(false);
	const status = data.status ?? "pending";

	const runCheck = () =>
		check.mutate(undefined, {
			onSuccess: (r) => {
				if (r.ok) {
					toast.success(t.number.checkOk, {
						description: r.quality_rating ? fill(t.number.checkQuality, { rating: r.quality_rating }) : undefined,
					});
				} else {
					toast.error(fill(t.number.checkFailed, { code: r.error_code ?? "?" }));
				}
			},
			onError: (e) => toast.error(messageFor(toApiError(e)).title),
		});

	return (
		<Panel
			title={t.number.title}
			aside={<StatusBadge status={VARIANT[status] ?? "pending"} label={t.number.status[status] ?? status} />}
		>
			<div className="grid gap-3 p-4" data-testid="wa-number">
				<p className="m-0 font-display text-2xl font-bold" data-testid="wa-phone">
					{data.display_phone}
				</p>
				<div className="m-0 grid gap-1 text-[13px]">
					{data.verified_name ? (
						<div className="flex flex-wrap gap-2">
							<span className="text-muted-foreground">{t.number.name}</span>
							<p className="m-0 font-semibold">{data.verified_name}</p>
						</div>
					) : null}
					{data.setup ? (
						<p className="m-0 text-muted-foreground">{fill(t.number.setup[data.setup], { product: productName })}</p>
					) : null}
					{data.meta_billing ? (
						<p className="m-0 text-muted-foreground">
							{fill(t.number.billing[data.meta_billing], { product: productName })}
						</p>
					) : null}
					{data.coexistence ? <p className="m-0 text-muted-foreground">{t.number.coexistence}</p> : null}
					{data.last_inbound_at ? (
						<p className="m-0 text-muted-foreground">
							{fill(t.chats.lastWrote, { when: formatRelative(data.last_inbound_at) })}
						</p>
					) : null}
				</div>
				{t.number.statusHelp[status] ? (
					<p className="m-0 flex items-start gap-2 text-[13px] text-foreground-2" data-testid="wa-status-help">
						<CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden />
						{t.number.statusHelp[status]}
					</p>
				) : null}
				<div className="flex flex-wrap gap-2">
					<Button variant="outline" size="sm" onClick={runCheck} aria-disabled={check.isPending || undefined}>
						{check.isPending ? t.number.checking : t.number.check}
					</Button>
					<OwnerGate action="whatsapp.manage">
						<Button variant="destructive" size="sm" onClick={() => setConfirm(true)}>
							<Unplug aria-hidden />
							{t.number.disconnect}
						</Button>
					</OwnerGate>
				</div>
			</div>
			<ConfirmDialog
				open={confirm}
				onOpenChange={setConfirm}
				title={t.number.disconnectTitle}
				body={t.number.disconnectBody}
				confirmLabel={t.number.disconnectConfirm}
				pending={disconnect.isPending}
				onConfirm={() =>
					disconnect.mutate(undefined, {
						onSuccess: () => {
							setConfirm(false);
							toast.success(t.number.disconnected);
						},
						onError: (e) => {
							setConfirm(false);
							toast.error(messageFor(toApiError(e)).title);
						},
					})
				}
			/>
		</Panel>
	);
}
