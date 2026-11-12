import { useQuery } from "@tanstack/react-query";
import { api, unwrap } from "@/lib/api/client";
import { formatDate } from "@/lib/format/date";
import { addOnStrings as s } from "@/lib/strings/billing-addons";
import { fill } from "@/lib/strings/en";

/** The workspace's own billing view (`GET /billing/account`): where the add-ons in force are listed. */
export const ownBillingQuery = {
	queryKey: ["billing", "account", "me"] as const,
	queryFn: () => unwrap(api().GET("/api/v1/billing/account")),
	retry: false,
	staleTime: 60_000,
};

/**
 * "Add-ons active" on the owner's Plan page (BIL5): what the provider added on top of the plan
 * ("+500 questions until 1 Nov"), without prices. Nothing is shown while there are none, while
 * billing is switched off or for a key that has no workspace view.
 */
export function ActiveAddOns() {
	const q = useQuery(ownBillingQuery);
	const items = q.data?.add_ons ?? [];
	if (items.length === 0) return null;
	return (
		<section
			aria-label={s.owner.title}
			data-testid="active-add-ons"
			className="min-w-0 rounded-lg border border-border bg-card"
		>
			<div className="border-b border-border px-4 py-3.5">
				<h2 className="text-[15px] font-bold">{s.owner.title}</h2>
				<p className="m-0 text-[12.5px] text-muted-foreground">{s.owner.lead}</p>
			</div>
			<ul className="m-0 grid list-none gap-0 p-0">
				{items.map((a) => (
					<li
						key={`${a.name}-${a.summary}-${a.ends_at ?? ""}`}
						className="grid gap-0.5 border-b border-dashed border-border px-4 py-2.5 last:border-b-0"
					>
						<span className="text-[13.5px] font-semibold">{a.name}</span>
						<span className="text-[13px] text-foreground-2">{a.summary}</span>
						{a.ends_at ? (
							<small className="text-[12px] text-muted-foreground">
								{fill(s.owner.until, { date: formatDate(a.ends_at) })}
							</small>
						) : null}
					</li>
				))}
			</ul>
		</section>
	);
}
