import { HttpResponse, http } from "msw";
import { BASE, BRANDING } from "../data";

export const brandingHandlers = [http.get(`${BASE}/api/v1/public/branding`, () => HttpResponse.json(BRANDING))];
