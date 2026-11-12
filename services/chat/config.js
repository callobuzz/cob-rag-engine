// Dashboard deployment defaults, loaded by index.html before its own script.
//
// These only decide where a fresh browser points before anyone touches the
// Settings panel; whatever a user saves there (localStorage) wins. The values
// below match the default `docker compose up` stack. Edit them if you serve the
// dashboard or the engine from somewhere else.
globalThis.RAG_CONFIG = {
  // Where the BROWSER reaches the RAG API.
  ragUrl: "http://127.0.0.1:8000",
  // Where the BROWSER reaches the scraper.
  scraperUrl: "http://127.0.0.1:8001",
};
