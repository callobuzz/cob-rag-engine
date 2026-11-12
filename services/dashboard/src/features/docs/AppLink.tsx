import { useRouter } from "@tanstack/react-router";
import type { AnchorHTMLAttributes, MouseEvent } from "react";

/**
 * A link to a path computed at run time (a help page's screen, a search hit). It is a real
 * `<a href>` (open in a new tab, copy the address) that navigates inside the app on a plain click.
 */
export function AppLink({ href, onClick, ...rest }: AnchorHTMLAttributes<HTMLAnchorElement> & { href: string }) {
	const router = useRouter();
	const go = (e: MouseEvent<HTMLAnchorElement>) => {
		onClick?.(e);
		if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
		e.preventDefault();
		void router.navigate({ href });
	};
	return <a href={href} onClick={go} {...rest} />;
}
