import { createFileRoute } from "@tanstack/react-router";
import { IndexPage } from "@/features/home/IndexPage";
import { overviewSearchSchema } from "@/lib/search";

// LP1 landing: when the public website is on, the root of the address belongs to it and a signed-in
// person's Home is `/app`. It is the same page as `/` (a hard load of `/` while the website is off
// still reaches the dashboard there).
export const Route = createFileRoute("/_app/app")({
	validateSearch: overviewSearchSchema,
	component: IndexPage,
});
