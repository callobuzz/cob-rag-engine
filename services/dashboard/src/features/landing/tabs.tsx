import type { TabKey } from "./draft";
import { HomeSectionsEditor } from "./HomeSectionsEditor";
import { LanguagesTab } from "./LanguagesTab";
import { LookTab } from "./LookTab";
import { MenuFooterTab } from "./MenuFooterTab";
import { PagesTab } from "./PagesTab";
import { PricingTab } from "./PricingTab";
import { VersionsTab } from "./VersionsTab";

/** The body of each tab. */
export function TabBodies({ tab }: { tab: TabKey }) {
	switch (tab) {
		case "home":
			return <HomeSectionsEditor />;
		case "look":
			return <LookTab />;
		case "pricing":
			return <PricingTab />;
		case "pages":
			return <PagesTab />;
		case "menu":
			return <MenuFooterTab />;
		case "languages":
			return <LanguagesTab />;
		case "versions":
			return <VersionsTab />;
	}
}
