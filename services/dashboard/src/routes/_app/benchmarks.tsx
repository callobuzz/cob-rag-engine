import { createFileRoute } from "@tanstack/react-router";
import { BenchmarksPage } from "@/features/benchmarks/BenchmarksPage";

export const Route = createFileRoute("/_app/benchmarks")({
	component: BenchmarksPage,
});
