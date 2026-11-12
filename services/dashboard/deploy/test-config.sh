#!/bin/sh
# Tests for 40-cob-config.sh (dashboard D12). POSIX sh, no dependencies:
#   sh deploy/test-config.sh            (from services/dashboard)
# or inside the image:
#   docker run --rm --entrypoint sh -v "$PWD/deploy:/t:ro" cob-rag-dashboard:local /t/test-config.sh
set -u

here=$(cd "$(dirname "$0")" && pwd)
script="$here/40-cob-config.sh"
config_template="$here/../public/config.js.template"
[ -r "$config_template" ] || config_template=/usr/share/nginx/html/config.js.template
nginx_template="$here/nginx.conf.template"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
failures=0
count=0

run() {
	# run <env assignments...>: renders into $work, output in $work/out, status in $status
	rm -f "$work/config.js" "$work/default.conf"
	env -i PATH="$PATH" COB_HTML_DIR="$work" COB_CONFIG_TEMPLATE="$config_template" \
		COB_NGINX_TEMPLATE="$nginx_template" COB_NGINX_CONF="$work/default.conf" "$@" \
		sh "$script" >"$work/out" 2>&1
	status=$?
}

check() {
	# check <description> <command...>
	count=$((count + 1))
	desc=$1
	shift
	if "$@"; then
		echo "ok   $desc"
	else
		echo "FAIL $desc"
		sed 's/^/     | /' "$work/out"
		failures=$((failures + 1))
	fi
}

has() { grep -qF -- "$2" "$1"; }
lacks() { ! grep -qF -- "$2" "$1"; }

# Defaults render.
run
check "defaults: exit 0" [ "$status" -eq 0 ]
check "defaults: ragUrl" has "$work/config.js" 'ragUrl: "http://127.0.0.1:8000",'
check "defaults: scraperUrl" has "$work/config.js" 'scraperUrl: "http://127.0.0.1:8001",'
check "defaults: usdToInr" has "$work/config.js" 'usdToInr: 85,'
check "defaults: RAG_CONFIG shape" has "$work/config.js" 'globalThis.RAG_CONFIG = {'
check "defaults: CSP connect-src" has "$work/default.conf" "connect-src 'self' http://127.0.0.1:8000 http://127.0.0.1:8001;"
check "defaults: widget-demo script-src" has "$work/default.conf" "script-src 'self' http://127.0.0.1:8000;"
check "defaults: no placeholder left" lacks "$work/default.conf" "__"
check "defaults: no unsafe-inline scripts" lacks "$work/default.conf" "script-src 'self' 'unsafe-inline'"

# TV6: only /whatsapp may load Meta's sign-up SDK; every other CSP stays free of Facebook.
fb_lines=$(grep -v '^[[:space:]]*#' "$work/default.conf" | grep -c 'connect.facebook.net' || true)
check "whatsapp: exactly one CSP names connect.facebook.net" [ "$fb_lines" -eq 1 ]
check "whatsapp: its location is exact" has "$work/default.conf" "location = /whatsapp {"
check "whatsapp: script-src allows the SDK" has "$work/default.conf" "script-src 'self' https://connect.facebook.net;"
check "whatsapp: frame-src allows the sign-up window" has "$work/default.conf" "frame-src 'self' https://www.facebook.com https://web.facebook.com;"
check "whatsapp: the SPA's other pages stay on script-src 'self'" has "$work/default.conf" "script-src 'self'; style-src"

# P1 payments: only /pay may load Razorpay's checkout; every other CSP stays free of Razorpay.
rzp_lines=$(grep -v '^[[:space:]]*#' "$work/default.conf" | grep -c 'razorpay.com' || true)
check "pay: exactly one CSP names razorpay.com" [ "$rzp_lines" -eq 1 ]
check "pay: its location is exact" has "$work/default.conf" "location = /pay {"
check "pay: script-src allows the checkout script" has "$work/default.conf" "script-src 'self' https://checkout.razorpay.com;"
check "pay: frame-src allows the payment window" has "$work/default.conf" "frame-src 'self' https://api.razorpay.com https://checkout.razorpay.com;"
check "pay: connect-src allows the API" has "$work/default.conf" "https://api.razorpay.com https://lumberjack.razorpay.com;"
# /P1 payments

# Custom values; trailing slashes normalised; CSP uses origins, config.js the full URL.
run RAG_DASHBOARD_ENGINE_URL=https://rag.sahayak.ai/ RAG_DASHBOARD_SCRAPER_URL=https://crawl.sahayak.ai/scraper// \
	RAG_DASHBOARD_USD_TO_INR=84.5
check "custom: exit 0" [ "$status" -eq 0 ]
check "custom: trailing slash removed" has "$work/config.js" 'ragUrl: "https://rag.sahayak.ai",'
check "custom: path kept, slashes removed" has "$work/config.js" 'scraperUrl: "https://crawl.sahayak.ai/scraper",'
check "custom: decimal rate" has "$work/config.js" 'usdToInr: 84.5,'
check "custom: CSP origins" has "$work/default.conf" "connect-src 'self' https://rag.sahayak.ai https://crawl.sahayak.ai;"
check "custom: widget-demo connect-src" has "$work/default.conf" "connect-src https://rag.sahayak.ai;"

# Invalid values: exit 1 with a message naming the variable, nothing rendered.
for bad in "ftp://rag.example" "127.0.0.1:8000" "http://" "javascript:alert(1)" "http://a b" \
	"http://x;script-src *" "http://x'" 'http://x"' "http://x/a|b"; do
	run RAG_DASHBOARD_ENGINE_URL="$bad"
	check "invalid engine URL '$bad': exit 1" [ "$status" -eq 1 ]
	check "invalid engine URL '$bad': message" has "$work/out" "RAG_DASHBOARD_ENGINE_URL must be an http:// or https:// URL"
	check "invalid engine URL '$bad': nothing written" [ ! -s "$work/config.js" ]
done
run RAG_DASHBOARD_SCRAPER_URL="not a url"
check "invalid scraper URL: exit 1" [ "$status" -eq 1 ]
check "invalid scraper URL: message" has "$work/out" "RAG_DASHBOARD_SCRAPER_URL must be"
for bad in "abc" "-5" "0" "0.0" "85 " "1e3"; do
	run RAG_DASHBOARD_USD_TO_INR="$bad"
	check "invalid rate '$bad': exit 1" [ "$status" -eq 1 ]
	check "invalid rate '$bad': message" has "$work/out" "RAG_DASHBOARD_USD_TO_INR must be a positive number"
done

# L5a demo
fa() { grep -v "razorpay.com" "$work/default.conf" | grep -o "frame-ancestors [^;]*;" | sort -u; }
fa_pay() { grep "razorpay.com" "$work/default.conf" | grep -o "frame-ancestors [^;]*;" | sort -u; }

run
check "frame-ancestors defaults to 'self'" [ "$status" -eq 0 ]
check "every CSP says frame-ancestors 'self' by default" [ "$(fa)" = "frame-ancestors 'self';" ]
check "pay: frame-ancestors is none by default" [ "$(fa_pay)" = "frame-ancestors 'none';" ]
check "no frame placeholder is left by default" lacks "$work/default.conf" "__FRAME_ANCESTORS__"

run RAG_FRAME_ANCESTORS=""
check "an empty value is 'self'" [ "$(fa)" = "frame-ancestors 'self';" ]

run RAG_FRAME_ANCESTORS="self https://demo.example.com"
check "A demo host allowed: exit 0" [ "$status" -eq 0 ]
check "A demo host allowed" [ "$(fa)" = "frame-ancestors 'self' https://demo.example.com;" ]
check "A demo host allowed in the other four CSPs" [ "$(grep -c 'frame-ancestors .self. https://demo.example.com;' "$work/default.conf")" -eq 4 ]
check "pay: still 'none' when the demo may frame the others" [ "$(fa_pay)" = "frame-ancestors 'none';" ]

run RAG_FRAME_ANCESTORS="none"
check "none alone" [ "$(fa)" = "frame-ancestors 'none';" ]

run RAG_FRAME_ANCESTORS="https://*.example.com self"
check "wildcard host" [ "$(fa)" = "frame-ancestors https://*.example.com 'self';" ]

run RAG_FRAME_ANCESTORS="https://demo.example.com:8443"
check "a port is allowed" [ "$(fa)" = "frame-ancestors https://demo.example.com:8443;" ]

for bad in "'self'" "http://demo.example.com" "https://a.example.com/path" "none self" \
	"https://a.example.com;script-src *" "*" "https://" "self,https://x.example.com" \
	"https://a.example.com'" "https://*" "https://a|b.example.com"; do
	run RAG_FRAME_ANCESTORS="$bad"
	check "refuses [$bad]" [ "$status" -ne 0 ]
	check "names the variable for [$bad]" grep -q "RAG_FRAME_ANCESTORS" "$work/out"
	check "writes nothing for [$bad]" [ ! -s "$work/default.conf" ]
done
# /L5a demo

echo "$((count - failures)) of $count checks passed"
[ "$failures" -eq 0 ]
