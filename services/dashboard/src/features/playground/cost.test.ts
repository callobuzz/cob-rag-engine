import { describe, expect, it } from "vitest";
import { answerCost, tokensFrom } from "./cost";
import { sessionTotals } from "./SessionTotals";
import type { BotMsg, KbSession } from "./session";

/** Only the fields the totals read; the rest of a message is the playground store's business. */
const bot = (over: Partial<BotMsg>) =>
	({ id: "b", role: "bot", status: "done", model: "openai/gpt-oss-120b", tokens: null, ...over }) as BotMsg;

const session = (...messages: BotMsg[]): KbSession => ({ messages, compares: [], activeId: null });

describe("real answer cost", () => {
	it("reads the engine's cost out of token_usage", () => {
		expect(
			tokensFrom({
				prompt_tokens: 1000,
				completion_tokens: 200,
				total_tokens: 1200,
				cost_usd: 0.0007,
				cost_known: true,
			}),
		).toEqual({ prompt: 1000, completion: 200, total: 1200, costUsd: 0.0007, costKnown: true });
		expect(tokensFrom({ prompt_tokens: 1, completion_tokens: 1, cost_usd: null, cost_known: false })).toMatchObject({
			costUsd: null,
			costKnown: false,
		});
	});

	it("answerCost: the engine's figure wins; the catalog estimate only fills a gap; else unknown", () => {
		const real = tokensFrom({ total_tokens: 10, cost_usd: 0.002, cost_known: true });
		expect(answerCost(real, 0.5)).toEqual({ usd: 0.002, known: true, estimated: false });
		const unpriced = tokensFrom({ total_tokens: 10, cost_usd: null, cost_known: false });
		expect(answerCost(unpriced, 0.5)).toEqual({ usd: null, known: false, estimated: false });
		const old = tokensFrom({ total_tokens: 10 });
		expect(answerCost(old, 0.5)).toEqual({ usd: 0.5, known: true, estimated: true });
		expect(answerCost(old, null)).toEqual({ usd: null, known: false, estimated: false });
		expect(answerCost(null, null).known).toBe(false);
	});

	it("session total sums known costs and counts the unpriced ones, never as free", () => {
		const priced = tokensFrom({ total_tokens: 10, cost_usd: 0.002, cost_known: true });
		const unpriced = tokensFrom({ total_tokens: 10, cost_usd: null, cost_known: false });
		const s = sessionTotals(
			session(bot({ tokens: priced }), bot({ id: "c", tokens: priced }), bot({ id: "d", tokens: unpriced })),
			[],
		);
		expect(s.questions).toBe(3);
		expect(s.usd).toBeCloseTo(0.004);
		expect(s.unknown).toBe(1);
		expect(s.estimated).toBe(0);
	});
});
