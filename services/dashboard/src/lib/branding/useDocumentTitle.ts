import { useEffect } from "react";
import { useBranding } from "./BrandingProvider";

/** `"{page} · {product_name}"`, or just the product name. */
export function documentTitle(page: string | null | undefined, productName: string): string {
	return page ? `${page} · ${productName}` : productName;
}

export function useDocumentTitle(page: string | null | undefined): void {
	const { productName } = useBranding();
	useEffect(() => {
		document.title = documentTitle(page, productName);
	}, [page, productName]);
}
