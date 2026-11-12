import { setupServer } from "msw/node";
import { analyticsHandlers } from "./handlers/analytics";
import { apiKeysHandlers } from "./handlers/api-keys";
import { assistantHandlers } from "./handlers/assistant";
import { authHandlers } from "./handlers/auth";
import { billingHandlers } from "./handlers/billing"; // BIL2
import { addOnHandlers } from "./handlers/billing-addons"; // BIL5
import { statementsHandlers } from "./handlers/billing-statements"; // BIL6
import { brandingHandlers } from "./handlers/branding";
import { cobHandlers } from "./handlers/cob";
import { cobAdminHandlers } from "./handlers/cob-admin";
import { publicCobHandlers } from "./handlers/cob-public";
import { conversationsHandlers } from "./handlers/conversations";
import { corporaHandlers } from "./handlers/corpora";
import { docsHandlers } from "./handlers/docs";
import { documentsHandlers } from "./handlers/documents";
import { evalsHandlers } from "./handlers/evals";
import { handoffsHandlers } from "./handlers/handoffs";
import { healthHandlers } from "./handlers/health";
import { homeHandlers } from "./handlers/home"; // BIZ3
import { identityHandlers } from "./handlers/identity";
import { ingestHandlers } from "./handlers/ingest";
import { invoicesHandlers } from "./handlers/invoices"; // P2 invoices
import { landingHandlers } from "./handlers/landing"; // LP4 landing
import { leadsHandlers } from "./handlers/leads";
import { legalHandlers } from "./handlers/legal"; // P3 legal
import { localLlmHandlers } from "./handlers/local-llm"; // PIP2
import { membersHandlers } from "./handlers/members";
import { modelsHandlers } from "./handlers/models";
import { onboardingHandlers } from "./handlers/onboarding"; // BIZ4
import { outcomesHandlers } from "./handlers/outcomes";
import { paymentsHandlers } from "./handlers/payments"; // P1 payments
import { pipelinesHandlers } from "./handlers/pipelines"; // PIP4
import { planChoiceHandlers } from "./handlers/plan-choice"; // P6 plan choice
import { plansHandlers } from "./handlers/plans";
import { profileHandlers } from "./handlers/profile";
import { queryHandlers } from "./handlers/query";
import { reviewHandlers } from "./handlers/review";
import { scraperHandlers } from "./handlers/scraper";
import { setupHandlers } from "./handlers/setup";
import { shopHandlers } from "./handlers/shop"; // P5 plans
import { supportHandlers } from "./handlers/support"; // TV3
import { tenantsHandlers } from "./handlers/tenants";
import { providerHandlers, usageHandlers } from "./handlers/usage";
import { valueHandlers } from "./handlers/value"; // TV4
import { whatsappHandlers } from "./handlers/whatsapp"; // TV6
import { widgetKeysHandlers } from "./handlers/widget-keys";

export const server = setupServer(
	...homeHandlers, // BIZ3: first, its /documents?source= falls through when there is no source
	...onboardingHandlers, // BIZ4
	...brandingHandlers,
	...authHandlers,
	...identityHandlers,
	...corporaHandlers,
	...modelsHandlers,
	...usageHandlers,
	...providerHandlers, // BIL1
	...analyticsHandlers,
	...assistantHandlers,
	...healthHandlers,
	...tenantsHandlers,
	...docsHandlers,
	...documentsHandlers,
	...ingestHandlers,
	...queryHandlers,
	...conversationsHandlers,
	...widgetKeysHandlers,
	...apiKeysHandlers,
	...plansHandlers,
	...pipelinesHandlers, // PIP4
	...localLlmHandlers, // PIP2
	...billingHandlers, // BIL2
	...addOnHandlers, // BIL5
	...statementsHandlers, // BIL6
	...membersHandlers,
	...profileHandlers,
	...scraperHandlers,
	...setupHandlers,
	...evalsHandlers,
	...reviewHandlers,
	...outcomesHandlers,
	...leadsHandlers,
	...handoffsHandlers,
	...cobHandlers,
	...cobAdminHandlers,
	...publicCobHandlers,
	...valueHandlers, // TV4
	...supportHandlers, // TV3
	...landingHandlers, // LP4 landing
	...legalHandlers, // P3 legal
	...paymentsHandlers, // P1 payments
	...invoicesHandlers, // P2 invoices
	...shopHandlers, // P5 plans
	...planChoiceHandlers, // P6 plan choice
	...whatsappHandlers, // TV6
);
