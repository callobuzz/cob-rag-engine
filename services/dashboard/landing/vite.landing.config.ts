import path from "node:path";
import { defineConfig } from "vite";

/**
 * The public website's own bundle (LP1): vanilla TypeScript and CSS, no React, no router.
 * Built into the engine's package so the Python image needs no Node at runtime. Three entries:
 * `core` (every page), `home` (the creative home page) and `pages` (pricing, contact ...).
 * The committed output carries no source maps.
 */
const here = import.meta.dirname;

export default defineConfig({
	root: here,
	base: "/_landing/build/",
	publicDir: false,
	build: {
		outDir: path.resolve(here, "../../../src/callobuzz_rag/landing/static/build"),
		// The fonts step writes into `fonts/` first, so the build must not empty the directory.
		emptyOutDir: false,
		manifest: "manifest.json",
		sourcemap: false,
		target: "es2020",
		// LP2 landing: modern CSS (light-dark(), view timelines) must reach the browser as written.
		cssTarget: ["chrome123", "safari17.5", "firefox120"],
		cssCodeSplit: true,
		minify: true,
		assetsInlineLimit: 0,
		rollupOptions: {
			input: {
				core: path.resolve(here, "src/core/index.ts"),
				home: path.resolve(here, "src/home/index.ts"),
				pages: path.resolve(here, "src/pages/index.ts"),
			},
			output: {
				entryFileNames: "assets/[name]-[hash].js",
				chunkFileNames: "assets/[name]-[hash].js",
				assetFileNames: "assets/[name]-[hash][extname]",
			},
		},
	},
});
