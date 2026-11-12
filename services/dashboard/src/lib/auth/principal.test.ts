import { describe, expect, it } from "vitest";
import { OPERATOR, PRIYA, SHREE, SV } from "@/test/msw/data";
import { inOperatorWorkspace, operatorWorkspace, principalFromSession, principalFromWhoami } from "./principal";

describe("principal scope", () => {
	it("the super admin with no workspace is the operator view", () => {
		const p = principalFromSession({ user: SV, tenant: null, tenants: [OPERATOR] });
		expect(p).toMatchObject({ view: "admin", role: null, tenant: null, superAdmin: true });
		expect(operatorWorkspace(p)?.id).toBe(OPERATOR.id);
		expect(inOperatorWorkspace(p)).toBe(false);
	});

	it("the super admin inside the operator workspace is a plain workspace principal (not admin)", () => {
		const p = principalFromSession({ user: SV, tenant: OPERATOR, tenants: [OPERATOR] });
		expect(p).toMatchObject({ view: "tenant", role: "owner", superAdmin: true });
		expect(p.tenant).toMatchObject({ id: OPERATOR.id, name: "Sahayak AI", isOperator: true });
		expect(inOperatorWorkspace(p)).toBe(true);
		// The way back is still offered.
		expect(operatorWorkspace(p)?.id).toBe(OPERATOR.id);
	});

	it("a customer's user never gets a workspace to switch into", () => {
		const p = principalFromSession({ user: PRIYA, tenant: SHREE, tenants: [SHREE] });
		expect(p).toMatchObject({ view: "tenant", superAdmin: false });
		expect(operatorWorkspace(p)).toBeNull();
		expect(inOperatorWorkspace(p)).toBe(false);
	});

	it("an admin key has no session to switch with", () => {
		const p = principalFromWhoami({ kind: "admin" } as Parameters<typeof principalFromWhoami>[0]);
		expect(p.view).toBe("admin");
		expect(operatorWorkspace(p)).toBeNull();
	});

	it("a super admin who deleted the workspace has nothing to switch into", () => {
		const p = principalFromSession({ user: SV, tenant: null, tenants: [] });
		expect(operatorWorkspace(p)).toBeNull();
	});
});
