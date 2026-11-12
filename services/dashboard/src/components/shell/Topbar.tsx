import { Menu, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { en } from "@/lib/strings/en";
import { AccountMenu } from "./AccountMenu";
import { ConsumptionChip } from "./ConsumptionChip";
import { GetHelpButton } from "./GetHelpButton"; // TV3
import { KbSwitcher } from "./KbSwitcher";
import { ScopeSwitcher } from "./ScopeSwitcher";
import { ThemeMenu } from "./ThemeMenu";

/** Mockup `.topbar`: menu (phones), KB switcher, search, view switcher (super admin), theme, account. 56 px. */
export function Topbar({ onOpenMenu, onOpenPalette }: { onOpenMenu: () => void; onOpenPalette: () => void }) {
	return (
		<header className="sticky top-0 z-[5] flex h-14 shrink-0 items-center gap-1.5 border-b border-border bg-[color-mix(in_srgb,var(--background)_88%,transparent)] px-3 backdrop-blur-[10px] min-[861px]:gap-2.5 min-[861px]:px-5">
			<Button
				variant="ghost"
				size="icon"
				className="min-[861px]:hidden"
				aria-label={en.shell.openMenu}
				onClick={onOpenMenu}
			>
				<Menu className="size-[18px]" aria-hidden />
			</Button>
			<KbSwitcher />
			<button
				type="button"
				onClick={onOpenPalette}
				aria-label={`${en.shell.search} (Ctrl K)`}
				className="ml-auto flex h-9 w-9 shrink-0 items-center justify-center gap-2.5 rounded-sm border border-border bg-muted text-muted-foreground hover:text-foreground min-[861px]:mx-auto min-[861px]:w-auto min-[861px]:max-w-[420px] min-[861px]:flex-1 min-[861px]:justify-start min-[861px]:pr-2.5 min-[861px]:pl-3"
			>
				<Search className="size-[18px] shrink-0" aria-hidden />
				<span className="hidden min-[861px]:inline">{en.shell.search}</span>
				<span className="kbd ml-auto hidden min-[861px]:inline">Ctrl K</span>
			</button>
			<ConsumptionChip />
			<GetHelpButton /> {/* TV3 */}
			<ScopeSwitcher />
			<ThemeMenu />
			<AccountMenu />
		</header>
	);
}
