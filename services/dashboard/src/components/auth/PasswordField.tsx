import { Eye, EyeOff } from "lucide-react";
import { type ComponentProps, useState } from "react";
import { Input } from "@/components/ui/input";
import { en } from "@/lib/strings/en";

/** Password input with a show/hide toggle (aria-pressed). Pass-through props for react-hook-form. */
export function PasswordField(props: Omit<ComponentProps<typeof Input>, "type">) {
	const [shown, setShown] = useState(false);
	return (
		<div className="relative">
			<Input {...props} type={shown ? "text" : "password"} className="pr-11" />
			<button
				type="button"
				aria-pressed={shown}
				aria-label={shown ? en.password.hide : en.password.show}
				onClick={() => setShown((s) => !s)}
				className="absolute top-1/2 right-1 grid size-8 -translate-y-1/2 place-items-center rounded-sm text-muted-foreground hover:bg-accent hover:text-foreground"
			>
				{shown ? <EyeOff className="size-4" aria-hidden /> : <Eye className="size-4" aria-hidden />}
			</button>
		</div>
	);
}
