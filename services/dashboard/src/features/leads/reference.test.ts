import { describe, expect, it } from "vitest";
import { normalizeReference } from "./reference";

describe("normalizeReference", () => {
	it.each([
		["SD-4F7K", "SD-4F7K"],
		["sd4f7k", "SD-4F7K"],
		["SD 4F7K", "SD-4F7K"],
		[" sd-4f7k. ", "SD-4F7K"],
	])("reads %s as %s", (raw, norm) => {
		expect(normalizeReference(raw)).toBe(norm);
	});

	it.each(["SD-4F7O", "SD-4F71", "SD-4F7", "S-4F7KK", "SD-4F7K9", "", "hello"])("refuses %s", (raw) => {
		expect(normalizeReference(raw)).toBeNull();
	});
});
