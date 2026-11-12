import { LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";
import { FormAlert } from "@/components/auth/FormAlert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { messageFor } from "@/lib/api/error-messages";
import { toApiError } from "@/lib/api/errors";
import { useBranding } from "@/lib/branding/BrandingProvider";
import { fill } from "@/lib/strings/en";
import { whatsappStrings as t } from "@/lib/strings/whatsapp";
import { type EmbeddedSignupBody, useEmbeddedSignup, type WhatsApp } from "./api";
import {
	createSignupCollector,
	type FacebookSdk,
	loadFacebookSdk,
	loginOptions,
	parseSignupMessage,
	unloadFacebookSdk,
} from "./signup";

type Signup = NonNullable<WhatsApp["embedded_signup"]>;

/**
 * "Connect with Meta" (TV6): opens Meta's sign-up window through the deployer's Meta app. The
 * Facebook SDK is added to the page when this button appears and taken away when it goes: this is
 * the only place in the dashboard that talks to Facebook. The window reports through a `message`
 * event, accepted from `https://www.facebook.com` only; the code and the session info are joined
 * and sent to the engine, which finishes the connection.
 */
export function EmbeddedSignupButton({
	kb,
	info,
	onConnected,
}: {
	kb: string;
	info: Signup;
	onConnected?: () => void;
}) {
	const { productName } = useBranding();
	const signup = useEmbeddedSignup(kb);
	const [fb, setFb] = useState<FacebookSdk | null>(null);
	const [sdkFailed, setSdkFailed] = useState(false);
	const [coexistence, setCoexistence] = useState(false);
	const [busy, setBusy] = useState<"idle" | "opening" | "connecting">("idle");
	const [failure, setFailure] = useState<string | null>(null);
	const collector = useRef<ReturnType<typeof createSignupCollector> | null>(null);

	useEffect(() => {
		let alive = true;
		loadFacebookSdk(info.app_id, info.graph_version)
			.then((sdk) => {
				if (alive) setFb(sdk);
			})
			.catch(() => {
				if (alive) setSdkFailed(true);
			});
		return () => {
			alive = false;
			unloadFacebookSdk();
		};
	}, [info.app_id, info.graph_version]);

	useEffect(() => {
		const onMessage = (e: MessageEvent) => {
			const event = parseSignupMessage(e.origin, e.data);
			if (event) collector.current?.setSession(event);
		};
		window.addEventListener("message", onMessage);
		return () => {
			window.removeEventListener("message", onMessage);
			collector.current?.dispose();
		};
	}, []);

	const start = () => {
		if (!fb) return;
		setFailure(null);
		setBusy("opening");
		collector.current?.dispose();
		collector.current = createSignupCollector({
			onComplete: (r) => {
				setBusy("connecting");
				const body: EmbeddedSignupBody = {
					code: r.code,
					phone_number_id: r.phoneNumberId,
					waba_id: r.wabaId,
					// Only Meta's own event says the number is also in the WhatsApp Business app; the
					// tick box only asks the window to offer that. A ticked box on a normal sign-up
					// must still register the number.
					coexistence: r.coexistence,
				};
				signup.mutate(body, {
					onSuccess: () => {
						setBusy("idle");
						toast.success(t.connect.success);
						onConnected?.();
					},
					onError: (err) => {
						setBusy("idle");
						const e = toApiError(err);
						const step = typeof e.extra.step === "string" ? e.extra.step : "";
						const known = e.code === "whatsapp_signup_failed" ? t.connect.failedStep[step] : undefined;
						setFailure(known ? fill(known, { product: productName }) : messageFor(e).title);
					},
				});
			},
			onCancel: () => {
				setBusy("idle");
				toast(t.connect.cancelled);
			},
			onError: () => {
				setBusy("idle");
				setFailure(t.connect.sdkFailed);
			},
			onIncomplete: () => {
				setBusy("idle");
				setFailure(t.connect.incomplete);
			},
		});
		fb.login(
			(response) => {
				const code = response.authResponse?.code;
				if (code) collector.current?.setCode(code);
				else collector.current?.abandon();
			},
			loginOptions(info.config_id, coexistence),
		);
	};

	return (
		<div className="grid gap-3" data-testid="embedded-signup">
			{failure ? <FormAlert>{failure}</FormAlert> : null}
			{sdkFailed ? <FormAlert>{t.connect.sdkFailed}</FormAlert> : null}
			<label htmlFor="wa-coexistence" className="flex items-start gap-2 text-[13px]">
				<Checkbox
					id="wa-coexistence"
					checked={coexistence}
					disabled={busy !== "idle"}
					onCheckedChange={(v) => setCoexistence(v === true)}
					aria-describedby="wa-coexistence-hint"
				/>
				<span className="grid gap-0.5">
					<span className="font-semibold">{t.connect.coexistence}</span>
					<small id="wa-coexistence-hint" className="text-muted-foreground">
						{t.connect.coexistenceHint}
					</small>
				</span>
			</label>
			<div>
				<Button
					type="button"
					variant="default"
					disabled={!fb}
					aria-busy={busy !== "idle" || undefined}
					aria-disabled={busy !== "idle" || undefined}
					onClick={() => (busy === "idle" ? start() : undefined)}
				>
					{busy !== "idle" ? <LoaderCircle className="animate-spin" aria-hidden /> : null}
					{busy === "opening" ? t.connect.opening : busy === "connecting" ? t.connect.working : t.connect.button}
				</Button>
			</div>
		</div>
	);
}
