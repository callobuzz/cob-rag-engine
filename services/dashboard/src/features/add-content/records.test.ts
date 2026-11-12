import { describe, expect, it } from "vitest";
import { batches, buildRecords, guessMapping, parseCsvRecords, parseJsonRecords } from "./records";

const LIMITS = { maxRecordChars: 100 };

describe("parsing sources", () => {
	it("JSON: an array of objects; columns in first-seen order", () => {
		const r = parseJsonRecords('[{"faq_id":"f1","question":"Is fasting needed?"},{"faq_id":"f2","answer":"Yes"}]');
		expect(r).toEqual({
			ok: true,
			source: {
				columns: ["faq_id", "question", "answer"],
				rows: [
					{ faq_id: "f1", question: "Is fasting needed?" },
					{ faq_id: "f2", answer: "Yes" },
				],
			},
		});
	});

	it("JSON: invalid text, wrong shape and empty arrays are explained", () => {
		expect(parseJsonRecords("{nope")).toMatchObject({ ok: false, error: expect.stringContaining("isn't valid JSON") });
		expect(parseJsonRecords('{"id":1}')).toMatchObject({
			ok: false,
			error: expect.stringContaining("array of objects"),
		});
		expect(parseJsonRecords("[1,2]")).toMatchObject({ ok: false });
		expect(parseJsonRecords("[]")).toMatchObject({ ok: false, error: "No records found." });
	});

	it("CSV: header row names the columns", () => {
		const r = parseCsvRecords("faq_id,question,answer\nf1,रिपोर्ट कब मिलेगी?,Same day by 7 pm");
		expect(r).toMatchObject({ ok: true, source: { columns: ["faq_id", "question", "answer"] } });
	});
});

describe("guessMapping", () => {
	it("recognises faq_id / answer / question / url and leaves the rest as metadata", () => {
		expect(guessMapping(["faq_id", "question", "answer", "link", "category", "language"])).toEqual({
			id: "faq_id",
			text: "answer",
			title: "question",
			url: "link",
			metadata: ["category", "language"],
		});
	});
});

describe("buildRecords", () => {
	const mapping = { id: "faq_id", text: "answer", title: "question", url: "", metadata: ["category", "fee", "tags"] };

	it("maps fields and stringifies metadata; empty metadata values are dropped", () => {
		const { records, errors } = buildRecords(
			[
				{
					faq_id: 7,
					answer: " Collection from home is free above ₹999. ",
					question: "Home collection?",
					category: "home",
					fee: 0,
					tags: ["a", "b"],
				},
			],
			mapping,
			LIMITS,
		);
		expect(errors).toEqual([]);
		expect(records).toEqual([
			{
				id: "7",
				text: "Collection from home is free above ₹999.",
				title: "Home collection?",
				metadata: { category: "home", fee: "0", tags: '["a","b"]' },
			},
		]);
	});

	it("reports missing id, duplicate id, empty text and too-long text per row", () => {
		const { records, errors } = buildRecords(
			[
				{ faq_id: "a", answer: "ok" },
				{ faq_id: "", answer: "no id" },
				{ faq_id: "a", answer: "dup" },
				{ faq_id: "b", answer: "  " },
				{ faq_id: "c", answer: "x".repeat(101) },
			],
			mapping,
			LIMITS,
		);
		expect(records.map((r) => r.id)).toEqual(["a"]);
		expect(errors).toEqual([
			{ row: 2, message: "No id" },
			{ row: 3, message: "Id “a” is used by row 1 too" },
			{ row: 4, message: "No text" },
			{ row: 5, message: "Text is 101 characters; the limit is 100" },
		]);
	});
});

describe("batches", () => {
	it("splits at the limit exactly", () => {
		const items = Array.from({ length: 2001 }, (_, i) => i);
		const b = batches(items, 1000);
		expect(b.map((x) => x.length)).toEqual([1000, 1000, 1]);
		expect(batches([1, 2], 1000)).toEqual([[1, 2]]);
		expect(batches([], 1000)).toEqual([]);
	});
});
