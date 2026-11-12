import type { components } from "./schema";

/** Short names for the billing types (BIL2). Generated: `pnpm gen:api` from docs/openapi.json. */
type S = components["schemas"];

export type PlanPricing = S["PlanPricingOut"];
export type PlanPricingRequest = S["PlanPricingRequest"];
export type PricingFields = S["PricingFields"];
export type BillingAccount = S["BillingAccountOut"];
export type BillingAccountRow = S["BillingAccountRow"];
export type UpdateBillingAccountRequest = S["UpdateBillingAccountRequest"];

// BIL3 ledger
export type LedgerEntry = S["LedgerEntryOut"];
export type LedgerList = S["LedgerListResponse"];
export type ManualEntryRequest = S["ManualEntryRequest"];
export type LedgerEntryKind = LedgerEntry["entry_type"];
export type Reconciliation = S["ReconciliationOut"];
export type ReconciliationFinding = S["ReconciliationFinding"];
// /BIL3 ledger

// BIL5 add-ons
export type AddOn = S["AddOnOut"];
export type AddOnKind = AddOn["kind"];
export type CreateAddOnRequest = S["CreateAddOnRequest"];
export type UpdateAddOnRequest = S["UpdateAddOnRequest"];
export type Grant = S["GrantOut"];
export type ApplyAddOnRequest = S["ApplyAddOnRequest"];
export type AddOnPayment = ApplyAddOnRequest["payment"];
export type ActiveAddOn = S["ActiveAddOn"];
// /BIL5 add-ons

// BIL6 statements
export type OwnBillingView = S["BillingAccountView"];
export type StatementSummary = S["StatementSummary"];
export type StatementRef = S["StatementRef"];
export type Statement = S["StatementOut"];
export type StatementSection = S["StatementSection"];
export type UsageBreakdownRow = S["UsageBreakdownRow"];
export type PnlReport = S["PnlOut"];
export type PnlRow = S["PnlRow"];
export type PnlTotals = S["PnlTotals"];
export type BillingMeter = S["BillingMeterOut"];
export type TopUpRequestBody = S["TopUpRequest"];
export type StatementFormat = "json" | "csv" | "html";
// /BIL6 statements

// TV3 notice
export type PricingPreview = S["PricingPreviewOut"];
export type PendingChange = S["PendingChangeOut"];
// /TV3 notice

// P5 plans
export type ShopItem = S["ShopItem"];
export type ShopList = S["ShopList"];
export type BuyAddOnRequest = S["BuyAddOnRequest"];
export type GrantView = S["GrantView"];
export type ServiceOrder = S["ServiceOrderView"];
export type ServiceOrderAdmin = S["ServiceOrderAdmin"];
export type ServiceOrderStatus = ServiceOrder["status"];
export type UpdateServiceOrderRequest = S["UpdateServiceOrderRequest"];
export type OwnKeyStatus = S["OwnKeyStatus"];
// /P5 plans

// P6 plan choice
export type PlanChoice = S["PlanChoice"];
export type PlanChoiceList = S["PlanChoiceList"];
export type PendingPlanChange = S["PendingPlanChange"];
export type ChangePlanResponse = S["ChangePlanResponse"];
export type PublicPlan = S["PublicPlan"];
// /P6 plan choice
