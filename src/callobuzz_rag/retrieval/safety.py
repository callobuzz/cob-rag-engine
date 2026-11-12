"""Prompt injection detection and content sanitization.

Ported from OpenClaw mining reference section 3:
- sanitize-for-prompt.ts: Unicode control char stripping
- external-content.ts: Suspicious pattern detection, homoglyph folding, content wrapping

Detection is LOG-ONLY — content is never blocked, only flagged.
"""

import logging
import re
import secrets
import unicodedata

from callobuzz_rag.core.config import Settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Suspicious pattern regexes (from OpenClaw external-content.ts, all 12)
# ---------------------------------------------------------------------------

SUSPICIOUS_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions?|prompts?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"disregard\s+(all\s+)?(previous|prior|above)",
        re.IGNORECASE,
    ),
    re.compile(
        r"forget\s+(everything|all|your)\s+(instructions?|rules?|guidelines?)",
        re.IGNORECASE,
    ),
    re.compile(
        r"you\s+are\s+now\s+(a|an)\s+",
        re.IGNORECASE,
    ),
    re.compile(
        r"new\s+instructions?:",
        re.IGNORECASE,
    ),
    re.compile(
        r"system\s*:?\s*(prompt|override|command)",
        re.IGNORECASE,
    ),
    re.compile(
        r"\bexec\b.*command\s*=",
        re.IGNORECASE,
    ),
    re.compile(
        r"elevated\s*=\s*true",
        re.IGNORECASE,
    ),
    re.compile(
        r"rm\s+-rf",
        re.IGNORECASE,
    ),
    re.compile(
        r"delete\s+all\s+(emails?|files?|data)",
        re.IGNORECASE,
    ),
    re.compile(
        r"</?system>",
        re.IGNORECASE,
    ),
    re.compile(
        r"\]\s*\n\s*\[?(system|assistant|user)\]?:",
        re.IGNORECASE,
    ),
]

# ---------------------------------------------------------------------------
# Homoglyph folding tables
# ---------------------------------------------------------------------------

# Fullwidth ASCII offsets
_FULLWIDTH_OFFSET_UPPER = 0xFF21 - ord("A")  # 65248
_FULLWIDTH_OFFSET_LOWER = 0xFF41 - ord("a")  # 65248
_FULLWIDTH_OFFSET_DIGIT = 0xFF10 - ord("0")  # 65248

# Angle bracket variants that should fold to < or >
_LEFT_ANGLE_VARIANTS: set[int] = {
    0x3008,  # CJK left angle bracket
    0x2329,  # Left-pointing angle bracket
    0x27E8,  # Mathematical left angle bracket
    0x276C,  # Medium left-pointing angle bracket ornament
    0x276E,  # Heavy left-pointing angle bracket ornament
    0x2770,  # Light left tortoise shell bracket ornament
    0xFF1C,  # Fullwidth less-than sign
    0xFE64,  # Small less-than sign
    0x226A,  # Much less-than
    0x2039,  # Single left-pointing angle quotation mark
    0x00AB,  # Left-pointing double angle quotation mark
}

_RIGHT_ANGLE_VARIANTS: set[int] = {
    0x3009,  # CJK right angle bracket
    0x232A,  # Right-pointing angle bracket
    0x27E9,  # Mathematical right angle bracket
    0x276D,  # Medium right-pointing angle bracket ornament
    0x276F,  # Heavy right-pointing angle bracket ornament
    0x2771,  # Light right tortoise shell bracket ornament
    0xFF1E,  # Fullwidth greater-than sign
    0xFE65,  # Small greater-than sign
    0x226B,  # Much greater-than
    0x203A,  # Single right-pointing angle quotation mark
    0x00BB,  # Right-pointing double angle quotation mark
}

# Boundary marker for content wrapping
_BOUNDARY_TAG = "EXTERNAL_UNTRUSTED_CONTENT"


def fold_homoglyphs(text: str) -> str:
    """Fold fullwidth ASCII and angle bracket variants to regular ASCII.

    Prevents attackers from using lookalike characters to inject markers
    that bypass simple string matching.
    """
    result: list[str] = []
    for ch in text:
        cp = ord(ch)

        # Fullwidth uppercase A-Z
        if 0xFF21 <= cp <= 0xFF3A:
            result.append(chr(cp - _FULLWIDTH_OFFSET_UPPER))
        # Fullwidth lowercase a-z
        elif 0xFF41 <= cp <= 0xFF5A:
            result.append(chr(cp - _FULLWIDTH_OFFSET_LOWER))
        # Fullwidth digits 0-9
        elif 0xFF10 <= cp <= 0xFF19:
            result.append(chr(cp - _FULLWIDTH_OFFSET_DIGIT))
        # Left angle bracket variants
        elif cp in _LEFT_ANGLE_VARIANTS:
            result.append("<")
        # Right angle bracket variants
        elif cp in _RIGHT_ANGLE_VARIANTS:
            result.append(">")
        else:
            result.append(ch)

    return "".join(result)


def sanitize_for_prompt_literal(text: str) -> str:
    """Strip Unicode control (Cc), format (Cf), and line/paragraph separators.

    These characters can break prompt structure and enable injection via
    invisible characters in document content or filenames.
    """
    result: list[str] = []
    for ch in text:
        cat = unicodedata.category(ch)
        # Keep normal whitespace (space, tab, newline, carriage return)
        if ch in (" ", "\t", "\n", "\r"):
            result.append(ch)
        # Strip control characters (Cc) and format characters (Cf)
        elif cat in ("Cc", "Cf") or ch in ("\u2028", "\u2029"):
            continue
        else:
            result.append(ch)
    return "".join(result)


def detect_suspicious_patterns(text: str) -> list[str]:
    """Check text against 12 known prompt injection patterns.

    Returns list of matched pattern descriptions. This is for logging
    only — content is never blocked.
    """
    matches: list[str] = []
    for pattern in SUSPICIOUS_PATTERNS:
        match = pattern.search(text)
        if match:
            matches.append(f"Pattern '{pattern.pattern}' matched: '{match.group()}'")
    return matches


def wrap_external_content(text: str, source_label: str | None = None) -> str:
    """Wrap text in clearly delimited tags to mark it as external content.

    Uses a random boundary ID to prevent marker spoofing. Strips any
    existing boundary markers from the content before wrapping.
    """
    boundary_id = secrets.token_hex(8)

    # Strip any existing boundary markers to prevent nested injection
    cleaned = text.replace(f"<<<{_BOUNDARY_TAG}", "").replace(f"<<<END_{_BOUNDARY_TAG}", "")

    source_attr = f' source="{source_label}"' if source_label else ""
    open_tag = f'<<<{_BOUNDARY_TAG} id="{boundary_id}"{source_attr}>>>'
    close_tag = f'<<<END_{_BOUNDARY_TAG} id="{boundary_id}">>>'

    return f"{open_tag}\n{cleaned}\n{close_tag}"


class ContentSanitizer:
    """Orchestrates content sanitization for retrieved chunks.

    Applies the full safety pipeline: homoglyph folding, Unicode sanitization,
    suspicious pattern detection (log-only), and external content wrapping.

    Injected into ContextAssembler (Epic 2-10) via constructor.
    """

    def __init__(self, settings: Settings) -> None:
        self._log_suspicious = settings.safety_log_suspicious

    def sanitize(self, text: str, source_label: str | None = None) -> str:
        """Apply the full safety pipeline to a chunk of text.

        Pipeline order:
        1. Fold homoglyphs (fullwidth -> ASCII, angle brackets)
        2. Strip Unicode control/format characters
        3. Detect suspicious patterns (log-only, never blocks)
        4. Wrap in external content tags
        """
        # Step 1: Fold homoglyphs
        text = fold_homoglyphs(text)

        # Step 2: Strip control characters
        text = sanitize_for_prompt_literal(text)

        # Step 3: Detect suspicious patterns (log-only)
        if self._log_suspicious:
            matches = detect_suspicious_patterns(text)
            if matches:
                label = source_label or "unknown"
                logger.warning(
                    "Suspicious patterns detected in chunk from '%s': %s",
                    label,
                    "; ".join(matches),
                )

        # Step 4: Wrap in external content tags
        text = wrap_external_content(text, source_label=source_label)

        return text

    def detect_only(self, text: str) -> list[str]:
        """Run detection without sanitization — for monitoring/analytics."""
        folded = fold_homoglyphs(text)
        cleaned = sanitize_for_prompt_literal(folded)
        return detect_suspicious_patterns(cleaned)
