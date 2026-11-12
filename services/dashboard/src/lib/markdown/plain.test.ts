import { describe, expect, it } from "vitest";
import { toPlainText } from "./plain";

describe("toPlainText", () => {
	it("drops markers and citation numbers", () => {
		expect(toPlainText("Open **Website widget** and copy the [link](https://x.test) [1].")).toBe(
			"Open Website widget and copy the link.",
		);
		expect(toPlainText("# Title\n\n- one\n- two")).toBe("Title one two");
	});
});
