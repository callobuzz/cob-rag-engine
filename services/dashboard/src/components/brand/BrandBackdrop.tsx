import "./brand.css";
/**
 * Non-logo visual for the auth brand panel when an operator supplies their own logo: concentric rings
 * and a dot grid in the operator's brand colour (CSS only, see brand.css). Decorative.
 */
export function BrandBackdrop() {
	return <div data-testid="brand-backdrop" className="brand-backdrop" aria-hidden="true" />;
}
