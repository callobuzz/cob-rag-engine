import random

from callobuzz_rag.website.dedupe import (
    NearDuplicateIndex,
    from_db,
    hamming,
    simhash,
    text_hash,
    to_db,
)


def page(seed: int, words: int = 400) -> str:
    rng = random.Random(seed)
    vocab = [f"w{n}" for n in range(5000)]
    return " ".join(rng.choice(vocab) for _ in range(words))


def test_identical_text_is_exact_even_with_different_spacing_and_case() -> None:
    index = NearDuplicateIndex()
    index.add("home", "Root canal from ₹4,500.  Call us today")
    assert index.find("root CANAL from ₹4,500 call us today") == ("home", "exact")


def test_one_changed_word_in_a_long_page_is_near() -> None:
    base = page(1).split()
    changed = base[:]
    changed[200] = "27-10-2026"  # "one changed date"
    index = NearDuplicateIndex()
    index.add("a", " ".join(base))
    assert hamming(simhash(" ".join(base)), simhash(" ".join(changed))) <= 3
    assert index.find(" ".join(changed)) == ("a", "near")


def test_different_pages_are_distinct() -> None:
    index = NearDuplicateIndex()
    index.add("root-canal", "Root canal treatment " + page(2, 120))
    assert index.find("Teeth whitening treatment " + page(3, 120)) is None


def test_band_index_does_not_scan_all_pairs() -> None:
    index = NearDuplicateIndex()
    n = 200
    for i in range(n):
        assert index.find(page(100 + i, 60)) is None
        index.add(i, page(100 + i, 60))
    long_page = page(999, 400)
    index.add("long", long_page)
    assert index.comparisons < n * n // 4
    base = long_page.split()
    base[10] = "changed"
    assert index.find(" ".join(base)) == ("long", "near")


def test_first_added_page_wins() -> None:
    index = NearDuplicateIndex()
    index.add("higher-ranked", page(7))
    assert index.find(page(7)) == ("higher-ranked", "exact")


def test_signed_storage_round_trip() -> None:
    for value in (0, 1, (1 << 63) - 1, 1 << 63, (1 << 64) - 1):
        stored = to_db(value)
        assert -(1 << 63) <= stored < (1 << 63)
        assert from_db(stored) == value


def test_devanagari_text_hashes_without_error() -> None:
    text = "रूट कैनाल ₹4,500 प्रति दाँत ।"
    assert len(text_hash(text)) == 64 and simhash(text) > 0
