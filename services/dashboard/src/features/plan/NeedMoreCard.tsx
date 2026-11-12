import { Mail } from "lucide-react";
import { ContactBox } from "@/components/auth/ContactBox";
import { CopyButton } from "@/components/patterns/CopyButton";
import { buttonVariants } from "@/components/ui/button";
import { Panel } from "@/features/kbs/ui";
import type { PlanUsageResponse } from "@/lib/api/accounts-types";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { formatDate } from "@/lib/format/date";
import { formatNumber } from "@/lib/format/number";
import { fill } from "@/lib/strings/en";
import { planPageStrings } from "@/lib/strings/overview";

const t = planPageStrings.needMore;

/** `mailto:` link with the subject "Upgrade {tenant}" (encoded). */
export function upgradeMailto(email: string, tenant: string): string {
	return `mailto:${email}?subject=${encodeURIComponent(fill(t.subject, { tenant }))}`;
}

/** "Need more?": upgrades are arranged with the operator (support e-mail and contact link from branding). */
export function NeedMoreCard({ tenant, plan }: { tenant: string; plan: PlanUsageResponse | undefined }) {
	const { productName, supportEmail } = useBranding();
	const limit = plan?.limits.monthly_questions;
	return (
		<Panel title={t.title} sub={fill(t.sub, { product: productName })}>
			<div className="grid gap-3 p-4">
				<p className="m-0 text-foreground-2">
					{limit != null && plan
						? fill(t.capped, { tenant, limit: formatNumber(limit), date: formatDate(plan.resets_at) })
						: fill(t.open, { product: productName })}
				</p>
				<ContactBox />
				{supportEmail ? (
					<div className="flex flex-wrap gap-2">
						<a className={buttonVariants({ variant: "default" })} href={upgradeMailto(supportEmail, tenant)}>
							<Mail aria-hidden />
							{t.email}
						</a>
						<CopyButton value={supportEmail} label={t.copy} ariaLabel={t.copyLabel} />
					</div>
				) : null}
			</div>
		</Panel>
	);
}
