import { describe, expect, it } from "vitest";
import { parseUserAgent } from "./ua";

const UA = {
	chromeWindows:
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
	edgeWindows:
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36 Edg/129.0.2792.52",
	safariIphone:
		"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Mobile/15E148 Safari/604.1",
	chromeIphone:
		"Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) CriOS/129.0.6668.69 Mobile/15E148 Safari/604.1",
	firefoxMac: "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.6; rv:130.0) Gecko/20100101 Firefox/130.0",
	firefoxLinux: "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0",
	chromeAndroid:
		"Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Mobile Safari/537.36",
	samsungAndroid:
		"Mozilla/5.0 (Linux; Android 14; SM-S921B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/26.0 Chrome/122.0.0.0 Mobile Safari/537.36",
	safariMac:
		"Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15",
	operaWindows:
		"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36 OPR/114.0.0.0",
};

describe("parseUserAgent", () => {
	it.each([
		[UA.chromeWindows, "Chrome on Windows", false],
		[UA.edgeWindows, "Edge on Windows", false],
		[UA.safariIphone, "Safari on iPhone", true],
		[UA.chromeIphone, "Chrome on iPhone", true],
		[UA.firefoxMac, "Firefox on macOS", false],
		[UA.firefoxLinux, "Firefox on Linux", false],
		[UA.chromeAndroid, "Chrome on Android", true],
		[UA.samsungAndroid, "Samsung Internet on Android", true],
		[UA.safariMac, "Safari on macOS", false],
		[UA.operaWindows, "Opera on Windows", false],
	])("%s → %s", (ua, label, mobile) => {
		const d = parseUserAgent(ua);
		expect(d.label).toBe(label);
		expect(d.mobile).toBe(mobile);
	});

	it("unknown or missing agents", () => {
		expect(parseUserAgent(null).label).toBe("Unknown device");
		expect(parseUserAgent("").label).toBe("Unknown device");
		expect(parseUserAgent("curl/8.5.0").label).toBe("Unknown browser");
		expect(parseUserAgent("SomeBot (Windows NT 10.0)").label).toBe("Browser on Windows");
	});
});
