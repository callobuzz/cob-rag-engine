# tests/unit/test_conv_claims.py
"""C1 gate 2: every price, date, number, contact and name in an answer must be in the
cited passages, the business card or the visitor's own question."""

import pytest

from callobuzz_rag.conversation.claims import (
    EXPLAINER_KINDS,
    Claim,
    ClaimIndex,
    ClaimKind,
    SentenceGate,
    extract_claims,
    normalize_citations,
    normalize_digits,
)
from callobuzz_rag.conversation.safety import EchoFilter

SOURCE = (
    "Home sample collection is free on orders above ₹999; otherwise Rs. 150 within Pune city. "
    "Lipid profile: fast for 10–12 hours. Reports in 24 hours. Call 98220 12345 or write to "  # noqa: RUF001
    "help@shree-labs.in. Book at https://shree-labs.in/book. Open 7 am to 8 pm. Dr Meera Kulkarni "
    "heads the lab at Kothrud Branch. Diwali offer valid till 14 November 2026. 20% off on Sundays."
)


def _values(text: str) -> set[tuple[str, str]]:
    return {(c.kind.value, c.value) for c in extract_claims(text)}


class TestExtraction:
    def test_money_normalised_to_paise(self) -> None:
        assert ("money", "99900") in _values("₹999")
        assert ("money", "15000") in _values("Rs. 150")
        assert ("money", "15000") in _values("150 rupees")
        assert ("money", "150000000") in _values("₹15 lakh")
        assert ("money", "2500000") in _values("INR 25k")
        assert ("money", "15000") in _values("१५० रुपये")

    def test_quantity_range_and_hindi_units(self) -> None:
        v = _values("fast for 10–12 hours")  # noqa: RUF001
        assert ("quantity", "10 hour") in v and ("quantity", "12 hour") in v
        assert ("quantity", "3 day") in _values("3 दिन में")

    def test_percent_date_time(self) -> None:
        assert ("percent", "20") in _values("20% off")
        assert ("date", "14-11-2026") in _values("till 14 November 2026")
        assert ("date", "14-11") in _values("on Nov 14th")
        assert ("date", "5-10-2026") in _values("05/10/2026")
        assert ("time", "07:00") in _values("7 am")
        assert ("time", "18:30") in _values("6:30 pm")
        assert ("hour", "9") in _values("सुबह 9 बजे")

    def test_contacts(self) -> None:
        assert ("phone", "9822012345") in _values("+91 98220-12345")
        assert ("phone", "18001234567") in _values("toll free 1800-123-4567")
        assert ("email", "help@shree-labs.in") in _values("Help@Shree-Labs.in")
        assert ("url", "shree-labs.in/book") in _values("https://www.shree-labs.in/book/.")

    def test_entities_title_case_runs(self) -> None:
        v = _values("Please visit Ruby Hall Clinic. Meet Dr Meera Kulkarni on Monday.")
        assert ("entity", "ruby hall clinic") in v
        assert ("entity", "dr meera kulkarni") in v
        assert not any(k == "entity" and "monday" in val for k, val in v)
        assert not any(k == "entity" and val.startswith("please") for k, val in v)

    def test_stoplist_hides_own_names(self) -> None:
        claims = extract_claims("Shree Labs is open.", stoplist=frozenset({"shree labs"}))
        assert not [c for c in claims if c.kind == ClaimKind.ENTITY]

    def test_devanagari_digits(self) -> None:
        assert normalize_digits("९८२२०१२३४५") == "9822012345"


class TestIndex:
    INDEX = ClaimIndex([SOURCE])

    @pytest.mark.parametrize(
        "text",
        [
            "Rs 150",
            "₹999",
            "10 hours",
            "12 hours",
            "24 hours",
            "98220 12345",
            "help@shree-labs.in",
            "https://shree-labs.in/book",
            "7 am",
            "8 pm",
            "Dr Meera Kulkarni",
            "Kothrud Branch",
            "14 Nov",
            "20%",
            "shree-labs.in",
        ],
    )
    def test_supported(self, text: str) -> None:
        assert all(self.INDEX.supports(c) for c in extract_claims(text)), text

    @pytest.mark.parametrize(
        "text",
        [
            "₹1,500",
            "48 hours",
            "98220 99999",
            "info@shree-labs.in",
            "https://shree-labs.in/offers",
            "9 pm",
            "Dr Anil Mehta",
            "15 November",
            "25%",
        ],
    )
    def test_unsupported(self, text: str) -> None:
        assert not all(self.INDEX.supports(c) for c in extract_claims(text)), text

    def test_bare_hour_matches_am_or_pm(self) -> None:
        assert ClaimIndex(["Open 7 am to 8 pm"]).supports(Claim(ClaimKind.HOUR, "8", "8 बजे"))


class TestSentenceGate:
    def _run(self, chunks: list[str], **kw: object) -> tuple[str, SentenceGate]:
        gate = SentenceGate(ClaimIndex([SOURCE]), **kw)  # type: ignore[arg-type]
        out: list[str] = []
        for c in chunks:
            out += gate.feed(c)
        out += gate.flush()
        return "".join(out), gate

    def test_releases_only_whole_checked_sentences(self) -> None:
        gate = SentenceGate(ClaimIndex([SOURCE]), mode="enforce")
        assert gate.feed("Home collection costs Rs") == []
        assert gate.feed(" 150 in Pune. Rep") == ["Home collection costs Rs 150 in Pune. "]

    def test_enforce_drops_unsupported_sentence(self) -> None:
        text, gate = self._run(
            ["Collection is ₹150. ", "Same-day reports cost ₹1,500 extra. ", "Call 98220 12345."],
            mode="enforce",
        )
        assert "1,500" not in text and "₹150" in text and "98220 12345" in text
        assert gate.removed == 1 and gate.verdicts[1].action == "removed"

    def test_warn_keeps_and_flags(self) -> None:
        text, gate = self._run(["Same-day reports cost ₹1,500 extra."], mode="warn")
        assert "₹1,500" in text and gate.flagged == 1 and gate.removed == 0

    def test_off_checks_nothing(self) -> None:
        text, gate = self._run(["Same-day reports cost ₹1,500 extra."], mode="off")
        assert "₹1,500" in text and gate.verdicts == []

    def test_decimals_and_abbreviations_do_not_split(self) -> None:
        gate = SentenceGate(ClaimIndex(["Fast 8.5 hours. Dr. Rao signs reports."]), mode="enforce")
        assert gate.feed("Fast 8.5 hours. ") == ["Fast 8.5 hours. "]
        assert gate.feed("Dr. Rao signs") == []

    def test_table_rows_are_sentences(self) -> None:
        text, gate = self._run(
            ["| Test | Price |\n|---|---|\n| CBC | ₹999 |\n| HbA1c | ₹4,000 |\n"], mode="enforce"
        )
        assert "₹999" in text and "₹4,000" not in text and gate.removed == 1

    def test_echo_filter_removes_prompt_leaks_even_in_warn_mode(self) -> None:
        echo = EchoFilter("Answer only from the CONTEXT and never invent prices or dates at all.")
        text, gate = self._run(
            ["Rule: answer only from the CONTEXT and never invent prices or dates at all. Hello."],
            mode="warn",
            echo=echo,
        )
        assert "never invent" not in text and gate.removed == 1

    def test_explainer_kinds_block_business_specifics(self) -> None:
        gate = SentenceGate(ClaimIndex([]), mode="enforce", kinds=EXPLAINER_KINDS)
        out = gate.feed("A lipid profile measures cholesterol. It costs about ₹500 here. ")
        out += gate.flush()
        assert "".join(out) == "A lipid profile measures cholesterol. "

    def test_citations_normalised(self) -> None:
        assert normalize_citations("Fast 10 hours【1】 and fact^2 and [^3].") == (
            "Fast 10 hours[1] and fact^2 and [3]."
        )
        text, _ = self._run(["Collection is ₹150【1】."], mode="enforce")
        assert text == "Collection is ₹150[1]."


class TestMarkdownAware:
    def _run(self, chunks: list[str], **kw: object) -> tuple[str, SentenceGate]:
        gate = SentenceGate(ClaimIndex([SOURCE]), **kw)  # type: ignore[arg-type]
        out: list[str] = []
        for c in chunks:
            out += gate.feed(c)
        out += gate.flush()
        return "".join(out), gate

    def test_exponents_and_bare_carets_are_left_to_the_renderers(self) -> None:
        assert normalize_citations("2^10 is 1024 and x^7") == "2^10 is 1024 and x^7"
        assert normalize_citations("fact^2 and [^3] and 【4】") == "fact^2 and [3] and [4]"

    def test_numbered_list_marker_is_not_a_boundary(self) -> None:
        text, gate = self._run(
            ["1. Fast for 10 hours. 2. Reports in 48 hours. 3. Reports in 24 hours.\n"]
        )
        assert "48" not in text
        assert "1. Fast for 10 hours. " in text and "3. Reports in 24 hours." in text
        assert "2." not in text  # the item is dropped with its marker, no orphan "2."
        assert gate.removed == 1

    def test_nested_bullets_stay_whole(self) -> None:
        text, gate = self._run(["- Collection\n  - Rs 150 in Pune\n  - Rs 777 extra\n"])
        assert "Rs 150" in text and "777 extra" not in text and gate.removed == 1

    def test_table_with_every_row_removed_leaves_nothing(self) -> None:
        text, gate = self._run(["| Test | Price |\n|---|---|\n| HbA1c | ₹4,000 |\n"])
        assert text == "" and "|" not in text
        assert gate.removed == 1

    def test_table_header_waits_for_a_passing_row(self) -> None:
        gate = SentenceGate(ClaimIndex([SOURCE]), mode="enforce")
        assert gate.feed("| Test | Price |\n|---|---|\n") == []
        assert gate.feed("| HbA1c | ₹4,000 |\n") == []
        out = gate.feed("| CBC | ₹999 |\n")
        assert "".join(out) == "| Test | Price |\n|---|---|\n| CBC | ₹999 |\n"

    def test_fenced_code_is_split_by_line_not_by_full_stop(self) -> None:
        gate = SentenceGate(ClaimIndex([SOURCE]), mode="enforce")
        assert gate.feed("```\nprice = 1.5. ok") == ["```\n"]
        assert gate.feed("\n```\n") == ["price = 1.5. ok\n", "```\n"]

    def test_link_is_not_split(self) -> None:
        text, gate = self._run(["See [pricing](https://shree-labs.in/book) now. Thanks."])
        assert text == "See [pricing](https://shree-labs.in/book) now. Thanks."
        assert gate.removed == 0
