/**
 * A small in-house user-agent parser for the sessions list (D7): "Chrome on Windows", "Safari on
 * iPhone". A3 stores only the user agent and IP, so this is all we can say about a device. Order
 * matters: Edge, Opera and Samsung Internet also say "Chrome"; Chrome also says "Safari".
 */
export interface Device {
	browser: string;
	os: string;
	mobile: boolean;
	label: string;
}

const BROWSERS: [RegExp, string][] = [
	[/\bEdg(?:e|A|iOS)?\//, "Edge"],
	[/\b(?:OPR|Opera)\//, "Opera"],
	[/\bSamsungBrowser\//, "Samsung Internet"],
	[/\b(?:Firefox|FxiOS)\//, "Firefox"],
	[/\b(?:Chrome|CriOS|Chromium)\//, "Chrome"],
	[/\bVersion\/[\d.]+.*\bSafari\//, "Safari"],
];

const SYSTEMS: [RegExp, string][] = [
	[/\biPhone\b/, "iPhone"],
	[/\biPad\b/, "iPad"],
	[/\bAndroid\b/, "Android"],
	[/\bCrOS\b/, "ChromeOS"],
	[/\bWindows\b/, "Windows"],
	[/\bMac OS X\b|\bMacintosh\b/, "macOS"],
	[/\bLinux\b/, "Linux"],
];

export function parseUserAgent(ua: string | null | undefined): Device {
	const s = ua?.trim() ?? "";
	if (!s) return { browser: "", os: "", mobile: false, label: "Unknown device" };
	const browser = BROWSERS.find(([re]) => re.test(s))?.[1] ?? "";
	const os = SYSTEMS.find(([re]) => re.test(s))?.[1] ?? "";
	const mobile = /\b(?:Mobile|iPhone|iPod)\b/.test(s) || (os === "Android" && !/\bTablet\b/.test(s));
	const label = browser && os ? `${browser} on ${os}` : browser || (os ? `Browser on ${os}` : "Unknown browser");
	return { browser, os, mobile, label };
}
