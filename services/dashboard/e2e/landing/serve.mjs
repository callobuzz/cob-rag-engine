// A tiny static server for the snapshot folders (LP2). No dependency: Node's own http.
//
//   node e2e/landing/serve.mjs <folder> [port]
//
// Every snapshot is a folder like <root>/<name>/index.html, rendered without a database. Their
// pages point at /_landing/build/..., so that prefix is served from one shared copy
// (`assets`). /widget.js is a stand-in so the "Ask it yourself" test never needs the engine.
import fs from "node:fs";
import http from "node:http";
import path from "node:path";
import { pathToFileURL } from "node:url";

export const PORT = 4310;

const TYPES = {
	".html": "text/html; charset=utf-8",
	".css": "text/css; charset=utf-8",
	".js": "text/javascript; charset=utf-8",
	".json": "application/json",
	".woff2": "font/woff2",
	".png": "image/png",
	".webp": "image/webp",
	".svg": "image/svg+xml",
};

export const WIDGET_STUB = "window.__widgetStubLoaded = (window.__widgetStubLoaded || 0) + 1;\n";

export function startServer(root, assets = root, port = PORT) {
	const base = path.resolve(root);
	const shared = path.resolve(assets);
	return new Promise((resolve, reject) => {
		const server = http.createServer((req, res) => {
			const url = new URL(req.url ?? "/", "http://127.0.0.1");
			let pathname = decodeURIComponent(url.pathname);
			if (pathname === "/widget.js") {
				res.writeHead(200, { "content-type": TYPES[".js"] });
				res.end(WIDGET_STUB);
				return;
			}
			if (pathname.endsWith("/")) pathname += "index.html";
			// /_landing/... comes from the shared copy, else from whichever snapshot wrote the file
			// (the backdrop snapshot has its own media).
			const asset = pathname.startsWith("/_landing/");
			const folder = asset ? shared : base;
			let file = path.resolve(folder, `.${pathname}`);
			if (!file.startsWith(folder)) {
				res.writeHead(403).end();
				return;
			}
			if (asset && !fs.existsSync(file)) {
				for (const name of fs.readdirSync(base)) {
					const other = path.resolve(base, name, `.${pathname}`);
					if (other.startsWith(base) && fs.existsSync(other)) {
						file = other;
						break;
					}
				}
			}
			fs.readFile(file, (error, data) => {
				if (error) {
					res.writeHead(404, { "content-type": "text/plain" }).end("not found");
					return;
				}
				res.writeHead(200, {
					"content-type": TYPES[path.extname(file)] ?? "application/octet-stream",
					"cache-control": "no-store",
				});
				res.end(data);
			});
		});
		server.on("error", reject);
		server.listen(port, "127.0.0.1", () => resolve(server));
	});
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
	const folder = process.argv[2];
	if (!folder) {
		process.stderr.write("usage: node serve.mjs <folder> [port]\n");
		process.exit(2);
	}
	const port = Number(process.argv[3] ?? PORT);
	await startServer(folder, folder, port);
	process.stdout.write(`serving ${folder} on http://127.0.0.1:${port}\n`);
}
