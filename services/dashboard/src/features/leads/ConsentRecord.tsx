import { formatDate } from "@/lib/format/date";
import { fill } from "@/lib/strings/en";
import { leadsStrings as t } from "@/lib/strings/leads";
import type { LeadDetail } from "./api";

/** What the visitor agreed to, in words an owner can show someone who asks. */
export function ConsentRecord({ consents }: { consents: LeadDetail["consents"] }) {
	const rows = consents ?? [];
	return (
		<section aria-labelledby="lead-consent" className="grid gap-1.5">
			<h3 id="lead-consent" className="text-[13px] font-semibold">
				{t.consent.title}
			</h3>
			{rows.length === 0 ? (
				<p className="m-0 text-[13px] text-muted-foreground">{t.consent.none}</p>
			) : (
				rows.map((c) => (
					<div key={`${c.consented_at}-${c.wording_hash}`} className="grid gap-1 text-[13px] text-foreground-2">
						<p className="m-0">
							{fill(t.consent.agreed, {
								date: formatDate(c.consented_at),
								version: String(c.notice_version),
								language: t.languages[c.language] ?? c.language,
								purpose: c.purpose,
								hash: c.wording_hash.slice(0, 12),
							})}
						</p>
						{c.withdrawn_at ? (
							<p className="m-0 font-medium text-destructive">
								{fill(t.consent.withdrawn, { date: formatDate(c.withdrawn_at) })}
							</p>
						) : null}
					</div>
				))
			)}
		</section>
	);
}
