// Dashboard deployment defaults, loaded by index.html before the app bundle.
// Same shape as the legacy dashboard's config.js. In the Docker image (D12) this file
// is generated from RAG_DASHBOARD_* variables; for `pnpm dev` edit it here.
globalThis.RAG_CONFIG = {
	// Where the BROWSER reaches the RAG API.
	ragUrl: "http://127.0.0.1:8000",
	// Where the BROWSER reaches the scraper.
	scraperUrl: "http://127.0.0.1:8001",
	// Display-only conversion for the ₹ / $ currency toggle (always labelled "estimate").
	usdToInr: 85,
};
