import "@testing-library/jest-dom/vitest";
import { cleanup, configure } from "@testing-library/react";
import { afterAll, afterEach, beforeAll } from "vitest";
import { resetPlaygroundSessions } from "@/features/playground/session";
import { closeBroadcast } from "@/lib/auth/broadcast";
import { resetSecretFragment } from "@/lib/auth/fragment";
import { resetRefreshState } from "@/lib/auth/refresh";
import { bindRuntime } from "@/lib/auth/runtime";
import { resetAuthState } from "@/lib/auth/store";
import { resetConfigCache } from "@/lib/config";
import { resetApiKeysState } from "./msw/handlers/api-keys";
import { resetAssistantState } from "./msw/handlers/assistant";
import { resetScopeCalls } from "./msw/handlers/auth";
import { resetBillingState, resetNoticeState } from "./msw/handlers/billing"; // BIL2, TV3
import { resetAddOnsState } from "./msw/handlers/billing-addons"; // BIL5
import { resetStatementsState } from "./msw/handlers/billing-statements"; // BIL6
import { resetConversationsState } from "./msw/handlers/conversations";
import { resetCorporaState } from "./msw/handlers/corpora";
import { resetDocsState } from "./msw/handlers/docs";
import { resetDocumentsState } from "./msw/handlers/documents";
import { resetHomeState } from "./msw/handlers/home"; // BIZ3
import { resetIngestState } from "./msw/handlers/ingest";
import { resetInvoicesState } from "./msw/handlers/invoices"; // P2 invoices
import { resetLandingState } from "./msw/handlers/landing"; // LP4 landing
import { resetLegalState } from "./msw/handlers/legal"; // P3 legal
import { resetLocalLlmState } from "./msw/handlers/local-llm"; // PIP2
import { resetTeamState } from "./msw/handlers/members";
import { resetOnboardingState } from "./msw/handlers/onboarding"; // BIZ4
import { resetPaymentsState } from "./msw/handlers/payments"; // P1 payments
import { resetFairUseState, resetPipelinesState } from "./msw/handlers/pipelines"; // PIP4
import { resetPlanChoiceState } from "./msw/handlers/plan-choice"; // P6 plan choice
import { resetPlansState } from "./msw/handlers/plans";
import { resetProfileState } from "./msw/handlers/profile";
import { resetQueryState } from "./msw/handlers/query";
import { resetScraperState } from "./msw/handlers/scraper";
import { resetSetupState } from "./msw/handlers/setup";
import { resetShopState } from "./msw/handlers/shop"; // P5 plans
import { resetSupportState } from "./msw/handlers/support"; // TV3
import { resetTenantsState } from "./msw/handlers/tenants";
import { resetConsumptionState } from "./msw/handlers/usage";
import { resetValueState } from "./msw/handlers/value"; // TV4
import { resetWhatsappState } from "./msw/handlers/whatsapp"; // TV6
import { resetWidgetKeysState } from "./msw/handlers/widget-keys";
import { server } from "./msw/server";
import { strictBodyViolations } from "./openapi-strict";

// A busy many-core run starves the event loop; findBy* must outlast it.
configure({ asyncUtilTimeout: 5000 });

// jsdom lacks these; Radix and cmdk use them.
class RO {
	observe() {}
	unobserve() {}
	disconnect() {}
}
globalThis.ResizeObserver ??= RO as unknown as typeof ResizeObserver;
if (!Element.prototype.scrollIntoView) Element.prototype.scrollIntoView = () => {};
// The router resets the shell's scroll region (#content) on navigation.
if (!Element.prototype.scrollTo) Element.prototype.scrollTo = () => {};
if (!Element.prototype.hasPointerCapture) Element.prototype.hasPointerCapture = () => false;
if (!Element.prototype.releasePointerCapture) Element.prototype.releasePointerCapture = () => {};
if (!Element.prototype.setPointerCapture) Element.prototype.setPointerCapture = () => {};
if (!window.matchMedia) {
	window.matchMedia = (query: string) =>
		({
			matches: false,
			media: query,
			onchange: null,
			addEventListener: () => {},
			removeEventListener: () => {},
			addListener: () => {},
			removeListener: () => {},
			dispatchEvent: () => false,
		}) as MediaQueryList;
}

// Every request body is checked against the engine's OpenAPI spec: the engine answers 422 to a
// field it does not declare, so a screen sending one must fail its test here (see openapi-strict.ts).
const bodyChecks: Promise<string[]>[] = [];
beforeAll(() => {
	server.listen({ onUnhandledRequest: "error" });
	server.events.on("request:start", ({ request }) => {
		bodyChecks.push(strictBodyViolations(request));
	});
});
afterEach(async () => {
	const violations = (await Promise.all(bodyChecks.splice(0))).flat();
	cleanup();
	server.resetHandlers();
	resetCorporaState();
	resetAssistantState();
	resetDocsState();
	resetDocumentsState();
	resetIngestState();
	resetQueryState();
	resetConversationsState();
	resetWidgetKeysState();
	resetConsumptionState();
	resetApiKeysState();
	resetTenantsState();
	resetScopeCalls();
	resetPlansState();
	resetPipelinesState(); // PIP4
	resetFairUseState(); // PIP4
	resetLocalLlmState(); // PIP2
	resetBillingState(); // BIL2
	resetNoticeState(); // TV3
	resetSupportState(); // TV3
	resetLandingState(); // LP4 landing
	resetLegalState(); // P3 legal
	resetPaymentsState(); // P1 payments
	resetInvoicesState(); // P2 invoices
	resetShopState(); // P5 plans
	resetPlanChoiceState(); // P6 plan choice
	resetWhatsappState(); // TV6
	resetAddOnsState(); // BIL5
	resetStatementsState(); // BIL6
	resetHomeState(); // BIZ3
	resetValueState(); // TV4
	resetOnboardingState(); // BIZ4
	resetTeamState();
	resetProfileState();
	resetScraperState();
	resetSetupState();
	resetPlaygroundSessions();
	window.sessionStorage.clear();
	window.localStorage.clear();
	resetAuthState();
	resetRefreshState();
	resetSecretFragment();
	resetConfigCache();
	closeBroadcast();
	bindRuntime({ navigate: () => {}, clearCache: () => {} });
	document.documentElement.className = "";
	if (violations.length) {
		const list = violations.join("\n  ");
		throw new Error(`The engine would refuse these request fields (422 extra_forbidden):\n  ${list}`);
	}
});
afterAll(() => server.close());
