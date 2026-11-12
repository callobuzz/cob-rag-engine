import { describe, expect, it } from "vitest";
import { sourceLabel } from "./source-labels";

// LP2 landing: the helper call that writes a draft conversation is metered under `landing`.
describe("landing usage source", () => {
	it("is labelled for people, not shown as a raw key", () => {
		expect(sourceLabel("landing")).toBe("Public website");
	});
});
