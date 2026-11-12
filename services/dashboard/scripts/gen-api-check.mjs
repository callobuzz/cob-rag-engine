// `pnpm gen:api:check`: regenerate the OpenAPI types into a temp file and fail when the
// committed src/lib/api/schema.d.ts is stale (CI).
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const committed = path.join(root, "src/lib/api/schema.d.ts");
const tmp = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "cob-gen-api-")), "schema.d.ts");
const bin = path.join(
	root,
	"node_modules/.bin",
	process.platform === "win32" ? "openapi-typescript.cmd" : "openapi-typescript",
);

execFileSync(bin, ["../../docs/openapi.json", "-o", tmp], {
	cwd: root,
	stdio: "ignore",
	shell: process.platform === "win32",
});
const normalise = (s) => s.replace(/\r\n/g, "\n");
if (normalise(fs.readFileSync(tmp, "utf8")) !== normalise(fs.readFileSync(committed, "utf8"))) {
	process.stderr.write("src/lib/api/schema.d.ts is stale: run `pnpm gen:api` and commit the result.\n");
	process.exit(1);
}
process.stdout.write("schema.d.ts is up to date.\n");
