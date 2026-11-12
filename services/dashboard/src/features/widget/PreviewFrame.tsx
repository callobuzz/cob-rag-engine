import { ExternalLink, Monitor, RefreshCw, ShieldPlus, Smartphone } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Segmented } from "@/components/patterns/Segmented";
import { Button } from "@/components/ui/button";
import { toastSaveError } from "@/features/kbs/detail/shared";
import { Panel } from "@/features/kbs/ui";
import { useRagUrl } from "@/lib/auth/store";
import { fill } from "@/lib/strings/en";
import { widgetStrings as t } from "@/lib/strings/widget";
import { cn } from "@/lib/utils";
import { useUpdateWidgetKey, type WidgetKey } from "./api";
import { previewAllowed } from "./schemas";

/** The page that hosts the real widget (public/widget-demo.html, same origin as the dashboard). */
export function demoUrl(publicKey: string, ragUrl: string): string {
	return `/widget-demo.html?${new URLSearchParams({ key: publicKey, api: ragUrl }).toString()}`;
}

/** What the live widget depends on: when it changes (save, rotate, pause) the iframe reloads. */
export function previewVersion(k: WidgetKey): string {
	return JSON.stringify([k.public_key, k.is_active, k.allowed_origins, k.appearance]);
}

/**
 * Live preview (D6): the real widget.js with this key inside `widget-demo.html`, at phone width or
 * desktop width. When the key doesn't list this dashboard's origin the widget would refuse to
 * answer, so an owner gets "Allow for previews" (adds exactly `location.origin`).
 */
export function PreviewFrame({ corpusId, widgetKey }: { corpusId: string; widgetKey: WidgetKey }) {
	const ragUrl = useRagUrl();
	const origin = window.location.origin;
	const [device, setDevice] = useState<"phone" | "desktop">("phone");
	const [reloads, setReloads] = useState(0);
	const update = useUpdateWidgetKey(corpusId);
	const allowed = previewAllowed(widgetKey.allowed_origins, origin);
	const src = demoUrl(widgetKey.public_key, ragUrl);

	const allow = async () => {
		try {
			await update.mutateAsync({
				id: widgetKey.id,
				body: { allowed_origins: [...widgetKey.allowed_origins, origin] },
			});
			toast.success(fill(t.preview.allowedToast, { origin, name: widgetKey.name }));
		} catch (e) {
			toastSaveError(e);
		}
	};

	return (
		<Panel
			title={t.preview.title}
			sub={t.preview.sub}
			aside={
				allowed ? (
					<div className="flex flex-wrap items-center gap-2">
						<Segmented
							label={t.preview.device}
							value={device}
							options={[
								{
									value: "phone",
									label: (
										<>
											<Smartphone className="size-3.5" aria-hidden />
											{t.preview.phone}
										</>
									),
								},
								{
									value: "desktop",
									label: (
										<>
											<Monitor className="size-3.5" aria-hidden />
											{t.preview.desktop}
										</>
									),
								},
							]}
							onChange={setDevice}
						/>
						<Button
							type="button"
							size="sm"
							variant="ghost"
							aria-label={t.preview.reloadLabel}
							title={t.preview.reloadLabel}
							onClick={() => setReloads((n) => n + 1)}
						>
							<RefreshCw aria-hidden />
						</Button>
						<a
							href={src}
							target="_blank"
							rel="noopener noreferrer"
							className="inline-flex items-center gap-1 text-[12.5px] font-semibold"
						>
							<ExternalLink className="size-3.5" aria-hidden />
							{t.preview.openTab}
						</a>
					</div>
				) : null
			}
		>
			<div className="grid gap-3 p-4">
				{!widgetKey.is_active ? (
					<FormAlert tone="warn">
						<span data-testid="preview-paused">{t.preview.paused}</span>
					</FormAlert>
				) : null}
				{allowed ? (
					<div
						className={cn(
							"mx-auto w-full overflow-hidden rounded-lg border border-border bg-white",
							device === "phone" ? "max-w-[390px]" : "max-w-none",
						)}
						data-device={device}
					>
						<iframe
							key={`${previewVersion(widgetKey)}#${reloads}`}
							src={src}
							title={t.preview.frameTitle}
							sandbox="allow-scripts allow-same-origin allow-popups"
							className="block h-[640px] w-full border-0"
						/>
					</div>
				) : (
					<div
						className="grid justify-items-start gap-3 rounded-lg border border-dashed border-input bg-muted p-5"
						data-testid="preview-origin"
					>
						<h3 className="text-[15px] font-bold">{fill(t.preview.originTitle, { origin })}</h3>
						<p className="m-0 text-[13px] text-foreground-2">{t.preview.originBody}</p>
						<OwnerGate action="widgetKey.manage">
							<Button type="button" variant="default" disabled={update.isPending} onClick={() => void allow()}>
								<ShieldPlus aria-hidden />
								{t.preview.allow}
							</Button>
						</OwnerGate>
					</div>
				)}
			</div>
		</Panel>
	);
}
