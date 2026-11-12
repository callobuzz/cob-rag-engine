import { HomePage } from "@/features/home/HomePage";
import { OverviewPage } from "@/features/overview/OverviewPage";
import { usePrincipal } from "@/lib/auth/store";

/** BIZ3: a business lands on its plain Home; the operator keeps the engine Overview. Shared by `/` and `/app`. */
export function IndexPage() {
	const principal = usePrincipal();
	return principal?.view === "tenant" ? <HomePage /> : <OverviewPage />;
}
