import { useEffect, useId, useState } from "react";
import { toast } from "sonner";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { Panel } from "@/features/kbs/ui";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { usePrincipal } from "@/lib/auth/store";
import { can } from "@/lib/permissions";
import { websiteStrings } from "@/lib/strings/website";
import { useUpdateWebsite, type WebsiteOut } from "./api";
import { parsePatterns } from "./words";

const t = websiteStrings.settings;

/** Learning on or off, and the pages to leave out. Owners change it; members see it. */
export function LearningSettings({ kb, settings }: { kb: string; settings: WebsiteOut["settings"] }) {
	const update = useUpdateWebsite(kb);
	const principal = usePrincipal();
	const owner = can(principal, "website.manage");
	const patternsId = useId();
	const [text, setText] = useState(settings.exclude_patterns.join("\n"));
	const stored = settings.exclude_patterns.join("\n");
	useEffect(() => setText(stored), [stored]);
	const dirty = parsePatterns(text).join("\n") !== stored;

	const fail = (e: unknown) => {
		const m = messageFor(toApiError(e));
		toast.error(m.title, m.body ? { description: m.body } : undefined);
	};

	return (
		<Panel title={t.title} sub={t.sub}>
			<div className="grid gap-4 p-4">
				<div className="flex items-start justify-between gap-4">
					<div className="grid gap-0.5">
						<Label htmlFor="website-enabled" className="text-[14px] font-semibold">
							{t.enabled}
						</Label>
						<span className="text-[12.5px] text-muted-foreground">{t.enabledHint}</span>
					</div>
					<OwnerGate action="website.manage">
						<Switch
							id="website-enabled"
							checked={settings.enabled}
							disabled={update.isPending}
							onCheckedChange={(enabled) => update.mutate({ enabled }, { onError: fail })}
						/>
					</OwnerGate>
				</div>
				<div className="grid gap-1.5">
					<Label htmlFor={patternsId} className="text-[14px] font-semibold">
						{t.patternsLabel}
					</Label>
					<Textarea
						id={patternsId}
						rows={4}
						value={text}
						placeholder={t.patternsPlaceholder}
						readOnly={!owner}
						className="font-mono"
						onChange={(e) => setText(e.target.value)}
						aria-describedby={`${patternsId}-hint`}
					/>
					<span id={`${patternsId}-hint`} className="text-[12.5px] text-muted-foreground">
						{owner ? t.patternsHint : t.ownersOnly}
					</span>
				</div>
				<div className="flex justify-end">
					<OwnerGate action="website.manage">
						<Button
							size="sm"
							disabled={!dirty || update.isPending}
							onClick={() =>
								update.mutate(
									{ exclude_patterns: parsePatterns(text) },
									{ onSuccess: () => toast.success(t.saved), onError: fail },
								)
							}
						>
							{t.save}
						</Button>
					</OwnerGate>
				</div>
			</div>
		</Panel>
	);
}
