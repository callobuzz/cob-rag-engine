import os from "node:os";
import path from "node:path";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// The tests read dates the way an Indian operator sees them (IST). Without this, a machine in UTC
// (GitHub's CI) shows a 00:00 IST change as the day before, and date assertions differ by machine.
process.env.TZ = "Asia/Kolkata";

export default defineConfig({
	plugins: [react()],
	resolve: {
		alias: { "@": path.resolve(import.meta.dirname, "src") },
	},
	test: {
		environment: "jsdom",
		globals: true,
		setupFiles: ["./src/test/setup.ts"],
		include: ["src/**/*.test.{ts,tsx}", "public/**/*.test.ts", "landing/src/**/*.test.ts"], // LP1 landing
		css: false,
		restoreMocks: true,
		testTimeout: 15000,
		// Half the cores (min 2, max 8): 28 workers starve each other and trip findBy* timeouts.
		maxWorkers: Math.min(8, Math.max(2, Math.floor(os.availableParallelism() / 2))),
	},
});
