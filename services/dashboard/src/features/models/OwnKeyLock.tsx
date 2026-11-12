import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Lock } from "lucide-react";
import { OwnerGate } from "@/components/patterns/OwnerGate";
import { Button } from "@/components/ui/button";
import { ownBillingQuery } from "@/features/billing/addons/ActiveAddOns";
import type { OwnKeyStatus } from "@/lib/api/billing-types";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { kbsStrings as kbs } from "@/lib/strings/kbs";
import { shopStrings as s } from "@/lib/strings/shop";

/**
 * Whether this workspace may use its own AI key (P5): `null` while unknown, for the operator's
 * view and wherever billing is off. Read from the workspace's billing view, which the Plan page
 * already loads, so there is one request for both.
 */
export function useOwnKeyStatus(): OwnKeyStatus | null {
	const q = useQuery(ownBillingQuery);
	return q.data?.byok ?? null;
}

/** "Pro", "Pro and Business", "Lite, Pro and Business". */
function joinNames(names: string[]): string {
	if (names.length < 2) return names.join("");
	return `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

/**
 * "Model & keys" without the allowance (P5): the key form is replaced by a plain note and a link
 * to the add-on. A key that was saved earlier stays saved and can be removed, but it is not used,
 * and the note says so (answers use the provider's AI and the balance).
 */
export function OwnKeyLock({ keySaved, onRemove }: { keySaved: boolean; onRemove: () => void }) {
	const { productName } = useBranding();
	const plans = useOwnKeyStatus()?.plans_with_byok ?? [];
	return (
		<fieldset
			data-testid="own-key-lock"
			className="m-0 grid min-w-0 gap-2 rounded-md border border-border bg-muted p-3"
			aria-label={s.ownKey.lockTitle}
		>
			<div className="flex flex-wrap items-start gap-3">
				<Lock className="mt-0.5 text-muted-foreground" aria-hidden />
				<div className="min-w-[200px] flex-1">
					<b>{s.ownKey.lockTitle}</b>
					<p data-testid="own-key-lock-body" className="m-0 text-[13px] text-foreground-2">
						{plans.length > 0
							? fill(s.ownKey.lockBody, { product: productName, plans: joinNames(plans) })
							: fill(s.ownKey.lockBodyNoPlan, { product: productName })}
					</p>
					{keySaved ? (
						<p data-testid="own-key-unused" className="m-0 mt-1 text-[13px] text-warning">
							{fill(s.ownKey.savedUnused, { product: productName })}
						</p>
					) : null}
				</div>
				<div className="flex flex-wrap items-center gap-2">
					<Button asChild size="sm" variant="outline">
						<Link to="/plan">{s.ownKey.lockLink}</Link>
					</Button>
					{keySaved ? (
						<OwnerGate action="kb.changeModel">
							<Button type="button" size="sm" variant="destructive" onClick={onRemove}>
								{kbs.model.clear}
							</Button>
						</OwnerGate>
					) : null}
				</div>
			</div>
		</fieldset>
	);
}
