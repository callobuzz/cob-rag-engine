import { useBranding } from "@/lib/branding/BrandingProvider";
import { en } from "@/lib/strings/en";

/** Mockup `.contact-box`: support e-mail (and the operator's contact link) from branding. */
export function ContactBox({ heading }: { heading?: string }) {
	const { supportEmail, contactUrl } = useBranding();
	return (
		<div className="grid w-full gap-1.5 rounded-md border border-border bg-card px-3.5 py-3 text-[13.5px]">
			{heading ? <b className="font-semibold">{heading}</b> : null}
			{supportEmail ? (
				<span>
					{en.contact.email}{" "}
					<a className="font-medium text-brand-text select-all" href={`mailto:${supportEmail}`}>
						{supportEmail}
					</a>
				</span>
			) : (
				<span className="text-foreground-2">Contact {en.contact.provider}.</span>
			)}
			{contactUrl ? (
				<a className="font-medium text-brand-text" href={contactUrl} target="_blank" rel="noopener noreferrer">
					{en.contact.link}
				</a>
			) : null}
		</div>
	);
}
