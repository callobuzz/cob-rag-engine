import { createRootRouteWithContext, Outlet, useRouter } from "@tanstack/react-router";
import { useEffect, useRef } from "react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { BrandingProvider, brandingQuery } from "@/lib/branding/BrandingProvider";
import type { RouterContext } from "../router";

export const Route = createRootRouteWithContext<RouterContext>()({
	// Branding is fetched once, before any page (and before sign-in). Failure → defaults.
	beforeLoad: async ({ context }) => {
		await context.queryClient.ensureQueryData(brandingQuery).catch(() => undefined);
	},
	component: Root,
});

/** After each client-side navigation, move focus to the page heading (D1 accessibility). */
function useFocusHeadingOnNavigate() {
	const router = useRouter();
	const first = useRef(true);
	useEffect(
		() =>
			router.subscribe("onResolved", () => {
				if (first.current) {
					first.current = false;
					return;
				}
				requestAnimationFrame(() => {
					const h = document.querySelector<HTMLElement>("main h1");
					const active = document.activeElement;
					// A page that autofocuses its only field (verify code) keeps that focus.
					if (active instanceof HTMLInputElement || active instanceof HTMLTextAreaElement) return;
					if (h && !h.contains(active)) {
						if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
						h.focus({ preventScroll: true });
					}
				});
			}),
		[router],
	);
}

function Root() {
	useFocusHeadingOnNavigate();
	return (
		<BrandingProvider>
			<TooltipProvider delayDuration={300}>
				<Outlet />
				<Toaster />
			</TooltipProvider>
		</BrandingProvider>
	);
}
