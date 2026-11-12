import { Monitor, Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
	DropdownMenu,
	DropdownMenuContent,
	DropdownMenuLabel,
	DropdownMenuRadioGroup,
	DropdownMenuRadioItem,
	DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { en } from "@/lib/strings/en";
import { setTheme, type ThemeChoice, useResolvedTheme, useTheme } from "@/lib/theme";

export function ThemeMenu() {
	const choice = useTheme();
	const resolved = useResolvedTheme();
	const Icon = choice === "system" ? Monitor : resolved === "dark" ? Moon : Sun;
	return (
		<DropdownMenu>
			<DropdownMenuTrigger asChild>
				<Button variant="ghost" size="icon" aria-label={`${en.shell.theme}: ${choice}`} className="text-foreground-2">
					<Icon className="size-[18px]" aria-hidden />
				</Button>
			</DropdownMenuTrigger>
			<DropdownMenuContent align="end" className="min-w-[180px]">
				<DropdownMenuLabel className="nav-label">{en.shell.theme}</DropdownMenuLabel>
				<DropdownMenuRadioGroup value={choice} onValueChange={(v) => setTheme(v as ThemeChoice)}>
					<DropdownMenuRadioItem value="light">
						<Sun aria-hidden /> {en.shell.light}
					</DropdownMenuRadioItem>
					<DropdownMenuRadioItem value="dark">
						<Moon aria-hidden /> {en.shell.dark}
					</DropdownMenuRadioItem>
					<DropdownMenuRadioItem value="system">
						<Monitor aria-hidden /> {en.shell.system}
					</DropdownMenuRadioItem>
				</DropdownMenuRadioGroup>
			</DropdownMenuContent>
		</DropdownMenu>
	);
}
