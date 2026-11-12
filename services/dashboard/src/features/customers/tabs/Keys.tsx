import { KeysPanel } from "@/features/keys/KeysPanel";
import type { AdminTenant } from "@/lib/api/accounts-types";

/**
 * The customer's `tk_` keys, for the operator: many named keys, each with a permission
 * (`/admin/tenants/{id}/keys`). A sign-up's workspace starts with none; "Create key" makes the
 * first.
 */
export function KeysTab({ tenant }: { tenant: AdminTenant }) {
	return <KeysPanel owner={{ kind: "customer", id: tenant.id, name: tenant.name }} />;
}
