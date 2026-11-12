import { createFileRoute } from "@tanstack/react-router";
import { SetupWizard } from "@/features/assistant-setup/SetupWizard";
import { setupSearchSchema } from "@/lib/search";

/** `/kbs/$kbId/setup?sid=`: the guided setup (C8). The trailing `_` un-nests it from `kbs.$kbId.tsx`, which renders no outlet. */
export const Route = createFileRoute("/_app/kbs/$kbId_/setup")({
	validateSearch: setupSearchSchema,
	component: SetupWizard,
});
