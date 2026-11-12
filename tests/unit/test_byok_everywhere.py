"""P5 T3: nothing reads a stored AI key without going through the own-key rule.

`billing/byok.py` decides whether a business may use its own key. A file that reads
`api_key_encrypted` must either be listed here with the reason it needs no allowance, or mention
`byok` (it applies the rule). A new reader fails this test with the rule named, so a forgotten
code path cannot let a plan without the allowance run on a key it does not include.
"""

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "callobuzz_rag"
KEY = re.compile(r"(?<![A-Za-z_])api_key_encrypted")  # not judge_api_key_encrypted

# Files that may name a stored key without applying the rule, and why.
ALLOWED = {
    "billing/byok.py": "the rule itself",
    "core/config_resolver.py": "resolves what `tiers.resolve.resolve_with_profile` hands it, "
    "after the byok strip (test_profile_everywhere.py: no surface calls it directly)",
    "core/reencrypt.py": "rotates the stored token, never uses it",
    "embeddings/factory.py": "the embedding key costs the operator nothing: out of scope (P5)",
    "api/schemas/config_self_service.py": "documentation of a redacted field",
    "api/schemas/corpus.py": "documentation of a redacted field",
    "db/models/corpus.py": "documentation of the column",
    "cob/orchestrator.py": "the COB system knowledge base only (the operator's own, exempt)",
}
# Files that read a stored key and apply the rule: each must say so.
APPLIES = {
    "billing/enforcement.py": "payer_hint",
    "cob/llm_choice.py": "COB answers for the person's workspace",
    "content_portal/services/ai_transformer.py": "the portal's transform pipeline",
    "analytics/consumption.py": "the owner's consumption view",
    "api/routes/corpus.py": "saving a key",
}


def readers() -> dict[str, str]:
    found = {}
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if KEY.search(text):
            found[path.relative_to(SRC).as_posix()] = text
    return found


def test_every_reader_of_a_stored_key_is_known_and_applies_the_rule() -> None:
    for name, text in readers().items():
        if name in ALLOWED:
            continue
        assert name in APPLIES, (
            f"{name} reads a stored AI key (api_key_encrypted). Whether a business may use its "
            "own key is billing/byok.py only (P5): apply `byok_allowed` / `usable_llm_config` "
            "there, then add the file to APPLIES in tests/unit/test_byok_everywhere.py."
        )
        assert "byok" in text.lower(), f"{name} reads a stored AI key but never mentions byok"


RAW_KB_CONFIG = re.compile(r"corpus_llm_config\s*=\s*[^\n]*corpus\.llm_config")


def test_nobody_hands_a_raw_kb_llm_config_to_the_resolver() -> None:
    """A stored key can also be read through `corpus.llm_config` without naming the field: the
    optimizer and the judge once did. Only the profile-aware resolver (after the strip) and COB's
    system KB may pass it raw."""
    skip = {"tiers/resolve.py", "cob/orchestrator.py", "core/config_resolver.py"}
    for path in SRC.rglob("*.py"):
        name = path.relative_to(SRC).as_posix()
        if name in skip:
            continue
        text = path.read_text(encoding="utf-8")
        assert not RAW_KB_CONFIG.search(text), (
            f"{name} passes corpus.llm_config straight to the resolver; wrap it in "
            "billing.byok.usable_llm_config(..., allowed=await byok_allowed(...))"
        )


def test_the_lists_name_real_files() -> None:
    for name in {*ALLOWED, *APPLIES}:
        assert (SRC / name).exists(), name


def test_the_answer_resolver_applies_the_rule_before_it_resolves() -> None:
    src = (SRC / "tiers/resolve.py").read_text(encoding="utf-8")
    strip = src.index("usable_llm_config(")
    first_resolve = src.index("resolver.resolve_llm(")
    assert strip < first_resolve
