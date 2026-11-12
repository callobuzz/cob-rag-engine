import { createFileRoute } from "@tanstack/react-router";
import { enginePageUrl } from "@/features/legal/api";
import { currentRagUrl } from "@/lib/auth/store";

const SLUG = /^[a-z][a-z-]{0,31}$/;

/**
 * `/legal/<page>` on the dashboard's own address (a link typed or pasted there): the engine serves
 * the page as plain HTML, so this only sends the browser on. Behind the proxy `/legal/*` already goes
 * to the engine and this route is never reached.
 */
export const Route = createFileRoute("/legal/$slug")({
	beforeLoad: ({ params }) => {
		if (SLUG.test(params.slug)) window.location.replace(enginePageUrl(currentRagUrl(), params.slug));
	},
	component: () => <p className="p-6 text-foreground-2">Opening the page…</p>,
});
