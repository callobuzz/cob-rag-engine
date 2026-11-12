"""TV6 review: bold italic and tables on WhatsApp."""

from callobuzz_rag.channels.whatsapp.render import to_whatsapp


def test_bold_italic_becomes_whatsapps_bold_italic() -> None:
    assert to_whatsapp("This is ***both*** and **bold** and *italic*.") == (
        "This is *_both_* and *bold* and _italic_."
    )


def test_a_two_column_table_reads_as_lines() -> None:
    table = "Fees:\n\n| Test | Price |\n|------|-------|\n| CBC | Rs 300 |\n| LFT | **Rs 450** |\n\nDone"
    assert to_whatsapp(table) == "Fees:\n\n• CBC: Rs 300\n• LFT: *Rs 450*\n\nDone"


def test_a_wider_table_names_each_column() -> None:
    table = "| Item | Price | Notes |\n|:---|---:|---|\n| Menu | Rs 5 | veg |\n| Tea | Rs 3 | |"
    assert to_whatsapp(table) == "• Menu — Price: Rs 5, Notes: veg\n• Tea — Price: Rs 3"


def test_a_pipe_in_ordinary_text_is_left_alone() -> None:
    assert to_whatsapp("Call 98220 | or write to us") == "Call 98220 | or write to us"
