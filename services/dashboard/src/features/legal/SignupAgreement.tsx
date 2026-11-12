import { useQuery } from "@tanstack/react-query";
import { Checkbox } from "@/components/ui/checkbox";
import { safeHttpUrl } from "@/lib/branding/BrandingProvider";
import { legalStrings as t } from "@/lib/strings/legal";
import { type PublicLegalItem, publicLegalQuery } from "./api";

export interface SignupLegal {
	/** Sign-up asks for acceptance: the Terms and the Privacy notice are both published. */
	required: boolean;
	terms: PublicLegalItem | null;
	privacy: PublicLegalItem | null;
	/** What to send as `accept_legal`: the versions this page is showing. */
	accept: { terms: number; privacy: number } | null;
	refetch: () => void;
}

/** The published Terms and Privacy notice, as sign-up must show them. An unreadable list asks for
 * nothing (the engine still refuses with `legal_acceptance_required` if it needs the box). */
export function useSignupLegal(): SignupLegal {
	const q = useQuery(publicLegalQuery);
	const terms = q.data?.find((p) => p.slug === "terms") ?? null;
	const privacy = q.data?.find((p) => p.slug === "privacy") ?? null;
	const required = terms != null && privacy != null;
	return {
		required,
		terms,
		privacy,
		accept: required ? { terms: terms.version, privacy: privacy.version } : null,
		refetch: () => void q.refetch(),
	};
}

function PageLink({ item, label }: { item: PublicLegalItem; label: string }) {
	const url = safeHttpUrl(item.url);
	return url ? (
		<a href={url} target="_blank" rel="noopener noreferrer" className="font-semibold text-brand-text underline">
			{label}
		</a>
	) : (
		<span className="font-semibold">{label}</span>
	);
}

/** "I agree to the Terms and have read the Privacy notice": a required box, with both pages opening in a new tab. */
export function SignupAgreement({
	legal,
	checked,
	onChange,
	error,
	id,
}: {
	legal: SignupLegal;
	checked: boolean;
	onChange: (checked: boolean) => void;
	error: boolean;
	id: string;
}) {
	if (!legal.required || !legal.terms || !legal.privacy) return null;
	const [before, middle, after] = t.signup.agree.split(/\{terms\}|\{privacy\}/);
	return (
		<div className="grid gap-1.5" data-testid="signup-agreement">
			<div className="flex items-start gap-2">
				<Checkbox
					id={id}
					checked={checked}
					aria-invalid={error || undefined}
					aria-describedby={error ? `${id}-error` : undefined}
					onCheckedChange={(v) => onChange(v === true)}
					className="mt-0.5"
				/>
				<label htmlFor={id} className="text-[13.5px] leading-snug">
					{before}
					<PageLink item={legal.terms} label={t.signup.terms} />
					{middle}
					<PageLink item={legal.privacy} label={t.signup.privacy} />
					{after}
				</label>
			</div>
			{error ? (
				<small id={`${id}-error`} className="text-[12.5px] font-medium text-destructive">
					{t.signup.required}
				</small>
			) : null}
		</div>
	);
}
