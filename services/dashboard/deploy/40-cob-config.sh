#!/bin/sh
# Runs from the nginx image's /docker-entrypoint.d before nginx starts (dashboard D12).
#
# Writes the dashboard's runtime config and its nginx site from the environment:
#   RAG_DASHBOARD_ENGINE_URL   where the BROWSER reaches the engine   (http://127.0.0.1:8000)
#   RAG_DASHBOARD_SCRAPER_URL  where the BROWSER reaches the scraper  (http://127.0.0.1:8001)
#   RAG_DASHBOARD_USD_TO_INR   display-only ₹ per $ rate              (85)
#   RAG_FRAME_ANCESTORS        who may frame the dashboard             (self; L5a demo)
#
# config.js keeps the RAG_CONFIG shape the dashboard reads; the nginx site carries a CSP whose
# connect-src names exactly these two origins. A bad value stops the container with a message
# naming the variable, instead of serving a dashboard that cannot reach anything.
#
# Paths can be overridden for tests (deploy/test-config.sh): COB_HTML_DIR, COB_CONFIG_TEMPLATE,
# COB_NGINX_TEMPLATE, COB_NGINX_CONF.
set -eu

html_dir="${COB_HTML_DIR:-/usr/share/nginx/html}"
config_template="${COB_CONFIG_TEMPLATE:-$html_dir/config.js.template}"
nginx_template="${COB_NGINX_TEMPLATE:-/etc/nginx/cob/nginx.conf.template}"
nginx_conf="${COB_NGINX_CONF:-/etc/nginx/conf.d/default.conf}"

me="40-cob-config"

fail() {
	echo "$me: $*" >&2
	exit 1
}

# An http(s) URL without trailing slashes. Only characters that are safe inside a JS string, a
# sed replacement and a CSP source list are accepted (no quotes, spaces, ';', ',', '&', '|').
url_value() {
	name=$1
	value=$2
	while [ "${value%/}" != "$value" ]; do value=${value%/}; done
	if ! printf '%s' "$value" |
		grep -Eq '^https?://([A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\])(:[0-9]{1,5})?(/[A-Za-z0-9._~/-]*)?$'; then
		fail "$name must be an http:// or https:// URL such as http://127.0.0.1:8000 (got '$2')"
	fi
	printf '%s' "$value"
}

# scheme://host[:port] — CSP sources are origins (a source with a path matches only that path).
origin_of() {
	printf '%s' "$1" | sed -E 's#^(https?://[^/]+).*$#\1#'
}

# L5a demo
# RAG_FRAME_ANCESTORS: who may show the dashboard in a frame (for example a live preview on your own site).
# A space-separated list. "self" and "none" are written WITHOUT quotes (a settings-file value that
# starts with a quote is read as a quoted string) and become 'self' / 'none'; every other item must
# be https://host or https://*.host (an optional port). Empty or unset means 'self'. "none" stands alone.
frame_ancestors_value() {
	raw=$1
	example="self https://demo.example.com"
	if [ -z "$(printf '%s' "$raw" | tr -d ' \t\r\n')" ]; then
		printf "'self'"
		return 0
	fi
	out=""
	items=0
	has_none=0
	set -f # a "*" in the value is text, never a file pattern
	for item in $raw; do
		items=$((items + 1))
		case $item in
		self) piece="'self'" ;;
		none)
			piece="'none'"
			has_none=1
			;;
		*)
			if ! printf '%s' "$item" |
				grep -Eq '^https://(\*\.)?[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:[0-9]{1,5})?$'; then
				fail "RAG_FRAME_ANCESTORS has an item that is not allowed: '$item'. Write a space-separated list such as: $example (self and none without quotes; every other item https://host)"
			fi
			piece=$item
			;;
		esac
		out="${out:+$out }$piece"
	done
	set +f
	if [ "$has_none" -eq 1 ] && [ "$items" -gt 1 ]; then
		fail "RAG_FRAME_ANCESTORS: none must be the only item (got '$raw'). Example: $example"
	fi
	printf '%s' "$out"
}
# /L5a demo

engine=$(url_value RAG_DASHBOARD_ENGINE_URL "${RAG_DASHBOARD_ENGINE_URL:-http://127.0.0.1:8000}")
scraper=$(url_value RAG_DASHBOARD_SCRAPER_URL "${RAG_DASHBOARD_SCRAPER_URL:-http://127.0.0.1:8001}")

# L5a demo
frame_ancestors=$(frame_ancestors_value "${RAG_FRAME_ANCESTORS:-}")
# /L5a demo

rate="${RAG_DASHBOARD_USD_TO_INR:-85}"
if ! printf '%s' "$rate" | grep -Eq '^[0-9]+(\.[0-9]+)?$' || printf '%s' "$rate" | grep -Eq '^0*(\.0*)?$'; then
	fail "RAG_DASHBOARD_USD_TO_INR must be a positive number such as 85 or 84.5 (got '$rate')"
fi

[ -r "$config_template" ] || fail "missing $config_template"
[ -r "$nginx_template" ] || fail "missing $nginx_template"

engine_origin=$(origin_of "$engine")
scraper_origin=$(origin_of "$scraper")

sed -e "s|__RAG_URL__|$engine|g" \
	-e "s|__SCRAPER_URL__|$scraper|g" \
	-e "s|__USD_TO_INR__|$rate|g" \
	"$config_template" >"$html_dir/config.js"

sed -e "s|__ENGINE_ORIGIN__|$engine_origin|g" \
	-e "s|__SCRAPER_ORIGIN__|$scraper_origin|g" \
	-e "s|__FRAME_ANCESTORS__|$frame_ancestors|g" \
	"$nginx_template" >"$nginx_conf"

echo "$me: engine $engine, scraper $scraper, usdToInr $rate, frame-ancestors $frame_ancestors"
