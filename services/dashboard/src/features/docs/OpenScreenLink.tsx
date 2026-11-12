import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useSelectedKb } from "@/components/shell/KbSwitcher";
import { buttonVariants } from "@/components/ui/button";
import { infoQuery } from "@/lib/api/queries";
import { usePrincipal } from "@/lib/auth/store";
import { useDashboardFeatures } from "@/lib/features"; // BIZ3
import { visiblePages } from "@/lib/nav";
import { docsStrings as t } from "@/lib/strings/docs";
import { cn } from "@/lib/utils";
import { AppLink } from "./AppLink";
import { screenTarget } from "./api";

/** "Open this screen →" on a screen's help page, only when the person has that screen. */
export function OpenScreenLink({ route }: { route: string | null }) {
	const principal = usePrincipal();
	const info = useQuery(infoQuery);
	const { selected } = useSelectedKb();
	const dashboard = useDashboardFeatures(); // BIZ3
	const nav = visiblePages(
		principal,
		{ content_portal: info.data?.features.content_portal, whatsapp: info.data?.features.whatsapp?.available },
		dashboard,
	);
	const target = screenTarget(route, nav, selected?.id ?? null);
	if (!target) return null;
	return (
		<AppLink href={target} className={cn(buttonVariants({ variant: "default", size: "sm" }), "no-underline")}>
			{t.openScreen}
			<ArrowRight aria-hidden />
		</AppLink>
	);
}
