import { afterEach, describe, expect, it } from "vitest";
import { formatCost, formatCostBoth } from "./cost";
import { getUsdInrRate, setEngineRate, toDisplayAmount } from "./currency";

afterEach(() => setEngineRate(null));

describe("formatCost", () => {
	it("never shows a zero for a price nobody knows", () => {
		expect(formatCost(null, false, "INR")).toBe("price unknown");
		expect(formatCost(undefined, undefined, "INR")).toBe("price unknown");
		expect(formatCost(0, false, "USD")).toBe("price unknown");
	});

	it("shows a floor when only part of the total is priced", () => {
		expect(formatCost(2, false, "USD")).toBe("≥ $2.00");
	});

	it("shows a real zero as a zero", () => {
		expect(formatCost(0, true, "INR")).toBe("₹0.00");
	});

	it("does not round a tiny real cost down to nothing", () => {
		// gpt-oss-120b at about $0.00002 an answer is ₹0.0017: not ₹0.00
		expect(formatCost(0.00002, true, "INR")).toBe("< ₹0.01");
		expect(formatCost(0.00002, true, "USD")).toBe("< $0.0001");
	});

	it("converts at the engine's rate once it is known", () => {
		expect(toDisplayAmount(1, "INR")).toBe(85);
		setEngineRate(84);
		expect(getUsdInrRate()).toBe(84);
		expect(toDisplayAmount(1, "INR")).toBe(84);
		expect(formatCost(1, true, "INR")).toBe("₹84");
	});

	it("ignores a rate that is not a positive number", () => {
		setEngineRate(0);
		setEngineRate(Number.NaN);
		expect(getUsdInrRate()).toBe(85);
	});

	it("shows both currencies when it knows the price, one phrase when it does not", () => {
		expect(formatCostBoth(0.5, true, "INR")).toBe("₹42.50 ($0.50)");
		expect(formatCostBoth(null, false, "INR")).toBe("price unknown");
	});
});
