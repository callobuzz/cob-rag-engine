import { describe, expect, it } from "vitest";
import { clampWidth, maxWidthFor } from "./useResizableWidth";

describe("panel width limits", () => {
	it("never takes the page below its sidebar plus 360 px, nor under the 360 px minimum", () => {
		expect(maxWidthFor(1440)).toBe(720);
		expect(maxWidthFor(1000)).toBe(392);
		expect(maxWidthFor(861)).toBe(360);
		expect(clampWidth(720, 1000)).toBe(392);
		expect(clampWidth(100, 1440)).toBe(360);
		expect(clampWidth(500, 1440)).toBe(500);
	});
});
