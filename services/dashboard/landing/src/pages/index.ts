// The script entry of pricing, features, about and contact. LP3 owns this folder: the plan
// toggles, the calculator and the contact form.
import "../../styles/pages.css";
import { initCalc } from "./calc";
import { initContact } from "./contact";
import { initToggles } from "./toggles";

function start(): void {
	initToggles();
	initCalc();
	initContact();
}

if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start, { once: true });
else start();
