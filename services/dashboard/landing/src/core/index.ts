import "../../styles/base.css";
import { initHeader } from "./header";
import { initLang } from "./lang";
import { initMobileBar } from "./mobile-bar";
import { initMotion } from "./motion";

function start(): void {
	initHeader();
	initMotion();
	initMobileBar();
	initLang();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
else start();
