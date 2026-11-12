// Playwright global setup for the landing specs: render the snapshots, serve them on 4310.
import type { Server } from "node:http";
import { startServer } from "./serve.mjs";
import { renderSnapshots, snapRoot } from "./snapshot";

export default async function setup(): Promise<() => Promise<void>> {
	const assets = renderSnapshots();
	const server: Server = await startServer(snapRoot(), assets);
	return async () => {
		await new Promise<void>((done) => server.close(() => done()));
	};
}
