import { ExternalLink, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { safeUrl } from "@/lib/markdown";
import { assistantStrings as t } from "@/lib/strings/assistant";

export interface Cta {
	type: string;
	label: string;
	url: string | null;
}

/**
 * The one call to action under an answer (C6). A link kind opens in a new tab and never renders
 * for an unsafe URL. The two form kinds (lead form, talk to a person) belong to the website
 * chat: where nothing handles them (`onAction` missing: the playground and the preview) they
 * show as a plain note rather than a button that does nothing.
 */
export function CtaButton({
	cta,
	onDismiss,
	onAction,
}: {
	cta: Cta;
	onDismiss: () => void;
	onAction?: (kind: "lead_form" | "handoff") => void;
}) {
	const dismiss = (
		<Button type="button" variant="ghost" size="icon" aria-label={t.replies.notNow} onClick={onDismiss}>
			<X aria-hidden />
		</Button>
	);
	if (cta.type === "lead_form" || cta.type === "handoff") {
		const kind = cta.type;
		const hint = kind === "lead_form" ? t.replies.opensLeadForm : t.replies.opensHandoff;
		return (
			<div className="flex flex-wrap items-center gap-1.5 pt-2" data-testid="cta">
				{onAction ? (
					<Button type="button" onClick={() => onAction(kind)}>
						{cta.label}
					</Button>
				) : (
					<>
						<Badge variant="info">{cta.label}</Badge>
						<span className="text-[12px] text-muted-foreground">{hint}</span>
					</>
				)}
				{dismiss}
			</div>
		);
	}
	const url = cta.url ? safeUrl(cta.url) : null;
	if (!url) return null;
	return (
		<div className="flex items-center gap-1 pt-2" data-testid="cta">
			<a href={url} target="_blank" rel="noopener noreferrer" className={buttonVariants({ variant: "default" })}>
				{cta.label}
				<ExternalLink aria-hidden />
			</a>
			{dismiss}
		</div>
	);
}
