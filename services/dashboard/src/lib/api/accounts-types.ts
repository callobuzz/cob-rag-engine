import type { components } from "./schema";

/**
 * Short names for the accounts types (A3–A6). Every one is GENERATED (`pnpm gen:api` from
 * docs/openapi.json); nothing here is written by hand. The temporary hand-written contract
 * (accounts-contract.ts) was deleted in D7 once A4–A6 reached openapi.json.
 */
type S = components["schemas"];

// A3: users, sessions, principal
export type TokenUser = S["UserInfo"];
export type TokenTenant = S["TenantInfo"];
export type TokenResponse = S["TokenResponse"];
export type MeResponse = S["MeResponse"];
export type SwitchTenantResponse = S["SwitchTenantResponse"];
export type SwitchAdminResponse = S["SwitchAdminResponse"];
export type TenantStatus = TokenTenant["status"];
export type MemberRole = TokenTenant["role"];
export type WhoAmI = S["WhoAmIResponse"];
export type EngineInfo = S["InfoResponse"];
export type PublicBranding = S["PublicBrandingResponse"];
export type SessionItem = S["SessionItem"];

// A4: sign-up and the tenant lifecycle
export type AdminTenant = S["AdminTenantResponse"];
export type AdminTenantList = S["TenantListResponse"];
export type OwnTenant = S["TenantResponse"];
export type CreateTenantRequest = S["CreateTenantRequest"];
export type CreateTenantResponse = S["CreateTenantResponse"];
export type UpdateTenantRequest = S["UpdateTenantRequest"];

// A5: teams and invitations
export type InvitationView = S["InvitationView"];
export type InvitationAccepted = S["AcceptedResponse"];
export type Member = S["MemberOut"];
export type Invitation = S["InvitationOut"];
export type AdminInvitation = S["AdminInvitationOut"];
export type Team = S["TeamResponse"];

// A6: plans and caps
export type Plan = S["PlanOut"];
export type PlanLimits = S["PlanLimits"];
export type PlanUsageResponse = S["PlanUsageResponse"];
export type CreatePlanRequest = S["CreatePlanRequest"];
export type UpdatePlanRequest = S["UpdatePlanRequest"];
