import path from "node:path";
import tailwindcss from "@tailwindcss/vite";
import { tanstackRouter } from "@tanstack/router-plugin/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// The router plugin must come before the React plugin (TanStack Router docs).
export default defineConfig({
	plugins: [tanstackRouter({ target: "react", autoCodeSplitting: true }), react(), tailwindcss()],
	resolve: {
		alias: { "@": path.resolve(import.meta.dirname, "src") },
	},
	// 127.0.0.1, not localhost: on Windows localhost resolves to ::1 (see CLAUDE.md).
	server: { host: "127.0.0.1", port: 5173, strictPort: true },
	preview: { host: "127.0.0.1", port: 4173, strictPort: true },
	build: {
		sourcemap: true,
		target: "es2022",
		// Fonts stay files: inlined as data: URIs they would need `font-src data:` in the image's CSP.
		assetsInlineLimit: (file) => (/\.(woff2?|ttf|otf)$/.test(file) ? false : undefined),
	},
});
