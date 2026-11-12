"""The website widget shows "cob-rag-engine by Callobuzz" as plain text by default; the operator can
hide it in Branding (D2). widget.js is a static script and cannot import the Python constant, so
these tests keep the two equal and make sure the line is never a link.
"""

import re
from importlib import resources

from callobuzz_rag.accounts.settings_store import POWERED_BY
from callobuzz_rag.api.schemas.widget import WidgetAppearance, WidgetConfigResponse
from callobuzz_rag.core.product import POWERED_BY_TEXT


def _script() -> str:
    # Read the way GET /widget.js serves it (api/routes/widget.py).
    return resources.files("callobuzz_rag.widget").joinpath("static/widget.js").read_text("utf-8")


def _function_body(source: str, name: str) -> str:
    """The body of `function name() {...}`, by brace matching."""
    start = source.index(f"function {name}(")
    open_at = source.index("{", start)
    depth = 0
    for i in range(open_at, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[open_at + 1 : i]
    raise AssertionError(f"unbalanced function {name}")


def test_constants_equal_powered_by() -> None:
    script = _script()
    assert dict(POWERED_BY) == {"text": POWERED_BY_TEXT}
    assert f'var POWERED_TEXT = "{POWERED_BY["text"]}";' in script
    assert "POWERED_URL" not in script
    assert "href=" not in script  # no link is built from a constant


# The footer follows the config: shown by default, null hides it (the operator's Branding switch)
def test_the_footer_is_plain_text_built_in_the_panel_from_the_config() -> None:
    script = _script()
    build = _function_body(script, "build")
    statement = build[build.index("root.innerHTML =") :]
    statement = statement[: statement.index(";")]
    assert (
        statement.index("</form>") < statement.index("footHtml()") < statement.index("</section>")
    )
    assert "?" not in statement.replace('"?"', "")
    assert not re.search(r"\bif\s*\(", statement)
    foot = _function_body(script, "footHtml")
    assert "cfg.powered_by" in foot and "esc(" in foot and "href" not in foot
    # a config without the key keeps the line: the default is the constant
    assert "powered_by: { text: POWERED_TEXT }" in script
    # null (hidden) must override the default although the loader skips nulls
    assert "remote.powered_by === null" in script


def test_the_footer_uses_a_muted_text_colour_not_the_brand_colour() -> None:
    css = _function_body(_script(), "css")
    rule = next(line for line in css.splitlines() if ".powered{" in line)
    # A fixed grey that stays readable (#4b5563 on white is about 7.6:1, WCAG AA at 11px);
    # no opacity, which dropped it to about 3.4:1.
    assert "color:#4b5563" in rule and "opacity" not in rule
    assert "+ color +" not in rule


def test_no_widget_option_can_hide_it() -> None:
    # Only the operator's Branding switch (`powered_by`, filled by the engine) can hide it;
    # no business-editable appearance option can
    appearance = set(WidgetAppearance.model_fields)
    assert not {f for f in appearance if "powered" in f or "brand" in f or "footer" in f}
    config = set(WidgetConfigResponse.model_fields)
    assert {f for f in config if "powered" in f or "brand" in f or "footer" in f} == {"powered_by"}
    assert WidgetAppearance.model_config.get("extra") == "forbid"
