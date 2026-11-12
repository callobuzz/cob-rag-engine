"""Pure helpers for the legal pages: fill placeholders and turn Markdown into safe HTML.

`to_html` is the only Markdown renderer for public pages. It parses with markdown-it-py's
CommonMark rules plus tables, with raw HTML switched off, so deployer text can never put a tag,
a script or an event handler on a public page. Links keep only http(s), mailto, tel, `#anchor`
and site-relative targets (anything else stays plain text, never an anchor) and every anchor
gets `rel="nofollow noopener"`.
"""

import re
from collections.abc import Mapping, Sequence
from typing import Final

from markdown_it import MarkdownIt
from markdown_it.renderer import RendererHTML
from markdown_it.token import Token
from markdown_it.utils import EnvType, OptionsDict

PLACEHOLDERS: Final[tuple[str, ...]] = (
    "product_name",
    "legal_name",
    "trade_name",
    "address",
    "city",
    "state",
    "email",
    "phone",
    "support_email",
    "grievance_name",
    "grievance_email",
    "grievance_address",
    "jurisdiction_city",
    "effective_date",
    "website",
)

_PLACEHOLDER = re.compile(r"\{\{([a-z_]+)\}\}")
_SAFE_PREFIXES: Final = ("http://", "https://", "mailto:", "tel:", "#")


def fill(template: str, values: Mapping[str, str]) -> str:
    """`{{name}}` replaced by `values[name]` in a single pass (never `str.format`, so braces in a
    template or a value mean nothing); a name with no value stays visible as `[[name]]`."""

    def one(match: re.Match[str]) -> str:
        name = match.group(1)
        value = values.get(name)
        return value if value else f"[[{name}]]"

    return _PLACEHOLDER.sub(one, template)


def used_placeholders(template: str) -> set[str]:
    return set(_PLACEHOLDER.findall(template))


def _valid_link(url: str) -> bool:
    lowered = url.lower()
    if any(ch.isspace() or ord(ch) < 32 for ch in url):  # these hide a scheme from a filter
        return False
    if lowered.startswith("//"):  # protocol-relative: leaves the site silently
        return False
    return lowered.startswith(_SAFE_PREFIXES) or lowered.startswith("/")


class _SafeRenderer(RendererHTML):
    def renderToken(  # noqa: N802 (markdown-it's name)
        self, tokens: Sequence[Token], idx: int, options: OptionsDict, env: EnvType
    ) -> str:
        if tokens[idx].type == "link_open":
            tokens[idx].attrSet("rel", "nofollow noopener")
        return super().renderToken(tokens, idx, options, env)


def _build() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False, "typographer": False}, renderer_cls=_SafeRenderer)
    md.enable("table")
    md.validateLink = _valid_link  # type: ignore[method-assign]
    return md


_MD: Final = _build()


def to_html(markdown: str) -> str:
    """Safe HTML for `markdown` (see the module docstring)."""
    return str(_MD.render(markdown))
