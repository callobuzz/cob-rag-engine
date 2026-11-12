import { describe, expect, it, vi } from "vitest";
import { onCobOpen, openCob } from "./cob-bus";

describe("cob-bus", () => {
	it("delivers an open request to every subscriber", () => {
		const a = vi.fn();
		const b = vi.fn();
		const offA = onCobOpen(a);
		const offB = onCobOpen(b);
		openCob({ prompt: "How do I rotate a key?", send: true });
		expect(a).toHaveBeenCalledWith({ prompt: "How do I rotate a key?", send: true });
		expect(b).toHaveBeenCalledTimes(1);
		offA();
		offB();
	});

	it("opens with no request at all", () => {
		const fn = vi.fn();
		const off = onCobOpen(fn);
		openCob();
		expect(fn).toHaveBeenCalledWith({});
		off();
	});

	it("stops delivering after unsubscribe", () => {
		const fn = vi.fn();
		const off = onCobOpen(fn);
		off();
		openCob({ docsSlug: "screens/widget" });
		expect(fn).not.toHaveBeenCalled();
	});
});
