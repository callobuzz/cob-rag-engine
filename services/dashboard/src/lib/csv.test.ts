import { describe, expect, it } from "vitest";
import { csvToObjects, parseCsv } from "./csv";

describe("parseCsv", () => {
	it("splits plain rows and cells", () => {
		expect(parseCsv("a,b,c\n1,2,3")).toEqual([
			["a", "b", "c"],
			["1", "2", "3"],
		]);
	});

	it("keeps commas and line breaks inside quotes", () => {
		expect(parseCsv('id,text\n1,"Fasting, 10–12 hours\nwater is fine"')).toEqual([
			["id", "text"],
			["1", "Fasting, 10–12 hours\nwater is fine"],
		]);
	});

	it("unescapes doubled quotes", () => {
		expect(parseCsv('q\n"Is the ""home collection"" free?"')).toEqual([["q"], ['Is the "home collection" free?']]);
	});

	it("handles CRLF and lone CR line endings", () => {
		expect(parseCsv("a,b\r\n1,2\r3,4\r\n")).toEqual([
			["a", "b"],
			["1", "2"],
			["3", "4"],
		]);
	});

	it("drops a UTF-8 byte-order mark", () => {
		expect(parseCsv("﻿faq_id,question\nf1,नमस्ते")).toEqual([
			["faq_id", "question"],
			["f1", "नमस्ते"],
		]);
	});

	it("skips empty lines but keeps rows of empty cells", () => {
		expect(parseCsv("a,b\n\n1,2\n\n,\n")).toEqual([
			["a", "b"],
			["1", "2"],
			["", ""],
		]);
	});

	it("keeps an empty quoted cell and a trailing empty cell", () => {
		expect(parseCsv('a,"",c,\n')).toEqual([["a", "", "c", ""]]);
	});

	it("returns ragged rows as written", () => {
		expect(parseCsv("a,b,c\n1\n1,2,3,4")).toEqual([["a", "b", "c"], ["1"], ["1", "2", "3", "4"]]);
	});
});

describe("csvToObjects", () => {
	it("maps rows by header, padding short rows and naming extra cells", () => {
		const t = csvToObjects("id,text\n1\n2,two,extra");
		expect(t.columns).toEqual(["id", "text", "column_3"]);
		expect(t.rows).toEqual([
			{ id: "1", text: "", column_3: "" },
			{ id: "2", text: "two", column_3: "extra" },
		]);
	});

	it("names blank and repeated headers", () => {
		expect(csvToObjects(" ,name,name\n1,2,3").columns).toEqual(["column_1", "name", "name_2"]);
	});

	it("an empty file has no columns and no rows", () => {
		expect(csvToObjects("")).toEqual({ columns: [], rows: [] });
	});
});
