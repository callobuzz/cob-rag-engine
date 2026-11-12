import { readFileSync } from "node:fs";
import { resolve } from "node:path";

/**
 * The engine refuses a request body with a field it does not declare (422 `extra_forbidden`,
 * every request model is `extra="forbid"`). The mock handlers answer anything, so a screen that
 * sent such a field would pass its test and fail against the real engine. This checks every JSON
 * body a test sends against the committed OpenAPI spec (`docs/openapi.json`) and reports the
 * fields the engine would refuse. `setup.ts` fails the test that sent them.
 */

type Schema = {
	$ref?: string;
	properties?: Record<string, Schema>;
	additionalProperties?: boolean | Schema;
	items?: Schema;
	anyOf?: Schema[];
	oneOf?: Schema[];
	allOf?: Schema[];
};
interface Op {
	requestBody?: { content?: Record<string, { schema?: Schema }> };
}
// Read at run time (not imported): the dashboard's Docker build context does not hold docs/.
const doc = JSON.parse(readFileSync(resolve(process.cwd(), "../../docs/openapi.json"), "utf8")) as {
	paths: Record<string, Record<string, Op>>;
	components: { schemas: Record<string, Schema> };
};

const routes = Object.entries(doc.paths).map(([template, ops]) => ({
	re: new RegExp(`^${template.replace(/[.*+?^$()|[\]\\]/g, "\\$&").replace(/\{[^}]+\}/g, "[^/]+")}/?$`),
	ops,
	params: (template.match(/{/g) ?? []).length,
}));

function deref(s: Schema): Schema {
	let cur = s;
	for (let i = 0; cur.$ref && i < 10; i++) {
		const name = cur.$ref.split("/").pop() ?? "";
		cur = doc.components.schemas[name] ?? {};
	}
	return cur;
}

/** Paths ("body.records.0.titel") of the fields `value` holds that `schema` does not declare. */
function unknownFields(schema: Schema, value: unknown, path: string, out: string[]): void {
	const s = deref(schema);
	const branches = [...(s.anyOf ?? []), ...(s.oneOf ?? [])];
	if (branches.length) {
		// A union: the value is fine if it fits any branch; report the closest miss otherwise.
		let best: string[] | null = null;
		for (const b of branches) {
			const found: string[] = [];
			unknownFields(b, value, path, found);
			if (found.length === 0) return;
			if (!best || found.length < best.length) best = found;
		}
		out.push(...(best ?? []));
		return;
	}
	for (const part of s.allOf ?? []) unknownFields(part, value, path, out);
	if (Array.isArray(value)) {
		const items = s.items as Schema | undefined;
		if (items) {
			for (const [i, v] of value.entries()) unknownFields(items, v, `${path}.${i}`, out);
		}
		return;
	}
	if (value === null || typeof value !== "object") return;
	for (const [k, v] of Object.entries(value as Record<string, unknown>)) {
		const prop = s.properties?.[k];
		if (prop) unknownFields(prop, v, `${path}.${k}`, out);
		else if (s.additionalProperties === false) out.push(`${path}.${k}`);
		else if (s.additionalProperties && typeof s.additionalProperties === "object")
			unknownFields(s.additionalProperties, v, `${path}.${k}`, out);
	}
}

/** The unknown fields of a request, or [] (no body, not JSON, or a route without a body schema). */
export async function strictBodyViolations(request: Request): Promise<string[]> {
	const type = request.headers.get("content-type") ?? "";
	if (!type.includes("application/json")) return [];
	let body: unknown;
	try {
		body = await request.clone().json();
	} catch {
		return [];
	}
	const url = new URL(request.url);
	const method = request.method.toLowerCase();
	// The most specific template wins (`/corpora/search` over `/corpora/{id}`).
	const match = routes
		.filter((r) => r.re.test(url.pathname) && r.ops[method]?.requestBody?.content?.["application/json"]?.schema)
		.sort((a, b) => a.params - b.params)[0];
	const schema = match?.ops[method]?.requestBody?.content?.["application/json"]?.schema;
	if (!schema) return [];
	const out: string[] = [];
	unknownFields(schema, body, "body", out);
	return out.map((p) => `${request.method} ${url.pathname}: ${p}`);
}
