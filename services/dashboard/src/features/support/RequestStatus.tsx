import { CircleCheck, CircleDashed, CirclePause, Clock } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { supportStrings } from "@/lib/strings/support";
import type { SupportStatus } from "./api";

const VARIANT = { open: "warn", answered: "ok", waiting: "info", closed: "default" } as const;
const ICON = { open: Clock, answered: CircleCheck, waiting: CirclePause, closed: CircleDashed } as const;

/** A request's status as icon and words (never colour alone), in the business's own terms. */
export function RequestStatus({ status, short = false }: { status: SupportStatus; short?: boolean }) {
	const { productName } = useBranding();
	const Icon = ICON[status];
	return (
		<Badge variant={VARIANT[status]} data-testid="request-status">
			<Icon aria-hidden />
			{short ? supportStrings.statusShort[status] : fill(supportStrings.status[status], { product: productName })}
		</Badge>
	);
}
