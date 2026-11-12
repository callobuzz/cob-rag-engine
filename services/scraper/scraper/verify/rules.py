"""scraper/verify/rules.py — Auto-verification rules for scraped content."""

from dataclasses import dataclass, field


@dataclass
class VerificationResult:
    """Outcome of verification rules."""

    status: str  # "verified", "flagged", or "failed"
    flag_reasons: list[str] = field(default_factory=list)


def verify_page(
    content: str,
    word_count: int,
    content_hash: str,
    existing_hashes: set[str],
    min_word_count: int = 50,
) -> VerificationResult:
    """Run all verification rules on a scraped page."""
    if not content or not content.strip():
        return VerificationResult(status="failed", flag_reasons=["Empty extraction"])

    reasons: list[str] = []

    if word_count < min_word_count:
        reasons.append(f"Low word count: {word_count} (minimum: {min_word_count})")

    if content_hash in existing_hashes:
        reasons.append("Duplicate content (same hash as another page in this job)")

    if reasons:
        return VerificationResult(status="flagged", flag_reasons=reasons)

    return VerificationResult(status="verified")
