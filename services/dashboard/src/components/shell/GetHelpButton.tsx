import { useQuery } from "@tanstack/react-query";
import { Link, useRouterState } from "@tanstack/react-router";
import { LifeBuoy } from "lucide-react";
import { Button } from "@/components/ui/button";
import { infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { openCob } from "@/lib/cob-bus";
import { supportStrings } from "@/lib/strings/support";

/**
 * "Get help" in the header of a business's dashboard (TV3): COB first. When COB is switched off on
 * the engine it goes straight to the support page, so a person always has a way to write to the
 * provider. Not shown to the provider's own view.
 */
export function GetHelpButton() {
	const principal = usePrincipal();
	const info = useQuery(infoQuery);
	const pathname = useRouterState({ select: (s) => s.location.pathname });
	if (principal?.view !== "tenant" || info.isPending) return null; // no flicker between the two variants
	const cob = info.data?.features?.cob?.enabled === true && principal.auth === "session";
	if (cob) {
		return (
			<Button
				variant="ghost"
				size="sm"
				data-testid="get-help"
				aria-label={supportStrings.title}
				onClick={() => openCob({})}
			>
				<LifeBuoy aria-hidden />
				<span className="hidden min-[861px]:inline">{supportStrings.title}</span>
			</Button>
		);
	}
	return (
		<Button asChild variant="ghost" size="sm" data-testid="get-help">
			<Link to="/support" search={{ new: 1, page: pathname.startsWith("/") ? pathname : undefined }}>
				<LifeBuoy aria-hidden />
				<span className="hidden min-[861px]:inline">{supportStrings.title}</span>
			</Link>
		</Button>
	);
}
