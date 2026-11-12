"""Generate the real-format documents for benchmarks/documents.py.

    python benchmarks/documents/make_fixtures.py

The generated files are committed, so running the benchmark needs none of this.
Regenerating needs python-docx, python-pptx, openpyxl and Pillow (all in the dev
environment) plus a Chromium browser (Chrome or Edge) to print the HTML handbook to
PDF with real CSS layout: two columns, running headers, a table across a page.

Every fact the questions ask about is a specific value (a price, a code, a count)
so a hit can be checked by string match against the retrieved chunk.
"""

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "files"

BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

HANDBOOK_HTML = """<!doctype html><html><head><meta charset="utf-8"><style>
@page { size: A4; margin: 22mm 18mm; }
body { font-family: Georgia, serif; font-size: 11pt; line-height: 1.45; }
header.run { position: fixed; top: -14mm; left: 0; right: 0; font-size: 8pt; color: #777;
  border-bottom: 1px solid #ccc; }
h1 { font-size: 20pt; } h2 { font-size: 14pt; margin-top: 18pt; }
.cols { column-count: 2; column-gap: 9mm; }
table { border-collapse: collapse; width: 100%; margin: 8pt 0; }
td, th { border: 1px solid #999; padding: 4pt 6pt; font-size: 10pt; text-align: left; }
.pb { page-break-before: always; }
</style></head><body>
<header class="run">Summit Trail Outfitters — Employee Handbook — Internal, revision 7</header>
<h1>Employee Handbook</h1>
<p>This handbook applies to every employee of Summit Trail Outfitters, in stores, in the
warehouse and at head office. It is revised every January; this is revision 7.</p>

<h2>Working hours</h2>
<div class="cols">
<p>Store staff work rotating shifts. A full-time shift is 8.5 hours including a 30-minute
unpaid meal break. Shift rotas are published every Thursday for the following two weeks.</p>
<p>Warehouse staff start at 6:45 am. Anyone who clocks in more than 10 minutes late three
times in a calendar month has a conversation with their team lead.</p>
<p>Head-office staff may work from home up to 2 days per week, agreed with their manager
in advance. Core hours are 10:00 to 15:30.</p>
<p>Overtime is paid at 1.5 times the base rate on weekdays and 2 times the base rate on
public holidays, and must be approved before it is worked.</p>
</div>

<h2>Paid leave</h2>
<p>Annual leave grows with length of service:</p>
<table>
<tr><th>Years of service</th><th>Annual leave days</th><th>Carry-over allowed</th></tr>
<tr><td>Less than 2</td><td>20</td><td>3 days</td></tr>
<tr><td>2 to 5</td><td>23</td><td>5 days</td></tr>
<tr><td>More than 5</td><td>26</td><td>8 days</td></tr>
</table>
<p>Parental leave is 18 weeks at full pay for the primary carer and 6 weeks for the
secondary carer. Bereavement leave is 5 days for immediate family.</p>

<h2 class="pb">Equipment and the staff discount</h2>
<p>Every new employee receives a uniform allowance of 180 dollars per year. Employees get
a 35 percent discount on full-price gear and 15 percent on sale items, capped at 2,500
dollars of purchases a year. The discount code format is STAFF followed by the
employee number.</p>
<p>Gear used for work, such as climbing ropes in the demo area, is inspected every
90 days and retired after 5 years regardless of condition.</p>

<h2>Safety</h2>
<p>Forklifts in the warehouse may only be driven by staff holding a current licence,
renewed every 3 years. The warehouse speed limit is 8 km/h. First-aid kits are checked
on the first Monday of every month; the checker signs the log kept in kit 04.</p>
<p>Report every incident, including near misses, within 24 hours using form HS-12.</p>

<h2>Expenses</h2>
<p>Travel is reimbursed at 0.42 dollars per kilometre for a private car. Hotel stays are
capped at 160 dollars per night outside capital cities and 240 dollars inside them.
Claims older than 60 days are not paid.</p>
</body></html>
"""

FAQ_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>Warranty FAQ</title></head>
<body>
<nav><a href="/">Home</a> | <a href="/shop">Shop</a> | <a href="/stores">Stores</a> |
<a href="/help">Help</a> | <a href="/account">Account</a></nav>
<main>
<h1>Warranty questions</h1>
<h2>How long is the warranty on tents?</h2>
<p>Tents carry a 7-year warranty against manufacturing defects in the fabric, poles and
zips. Damage from fire, UV exposure or pets is not covered.</p>
<h2>Are sleeping bags covered?</h2>
<p>Sleeping bags are covered for 3 years. Loss of loft from compressing a bag for long
storage is normal wear and is not a defect.</p>
<h2>What about boots?</h2>
<p>Boots have a 12-month warranty on the sole bond and stitching. Resoling by our partner
workshop costs 65 dollars and restarts a 6-month workmanship guarantee.</p>
<h2>How do I make a claim?</h2>
<p>Bring the item and proof of purchase to any store, or start online with claim form
W-3. Claims are assessed within 10 business days.</p>
<h2>Do you repair gear from other brands?</h2>
<p>Yes. Our repair desk fixes zips, seams and punctures on any brand, from 20 dollars.</p>
</main>
<footer>© Summit Trail Outfitters. Privacy | Terms | Cookies | Careers | Press</footer>
</body></html>
"""

RELEASE_NOTES = """# Store app release notes

## Version 4.2.0

Released on 14 March. Adds offline checkout: the till keeps working without a network
for up to 72 hours and syncs sales when it reconnects.

| Change | Ticket |
|---|---|
| Offline checkout | POS-1182 |
| Faster barcode scan | POS-1190 |
| Gift card balance on receipt | POS-1204 |

## Version 4.1.3

Fixes a rounding error on multi-buy discounts (ticket POS-1167) and raises the
receipt printer timeout to 8 seconds.

```bash
store-app --migrate --to 4.2.0
```

## Version 4.1.0

Adds click-and-collect lockers. A locker reservation is held for 48 hours.
"""


def make_docx(path: Path) -> None:
    from docx import Document

    doc = Document()
    doc.add_heading("Shipping policy", 0)
    doc.add_paragraph(
        "Orders placed before 2 pm on a business day leave our warehouse the same day. "
        "Orders over 150 dollars ship free within the domestic zone."
    )
    doc.add_heading("Delivery zones", level=1)
    table = doc.add_table(rows=1, cols=4)
    table.style = "Table Grid"
    for cell, text in zip(
        table.rows[0].cells, ["Zone", "Regions", "Delivery time", "Standard fee"], strict=True
    ):
        cell.text = text
    for row in [
        ("Zone A", "Metro areas", "1-2 business days", "6.95 dollars"),
        ("Zone B", "Regional towns", "3-4 business days", "9.95 dollars"),
        ("Zone C", "Remote areas and islands", "5-8 business days", "19.95 dollars"),
    ]:
        cells = table.add_row().cells
        for cell, text in zip(cells, row, strict=True):
            cell.text = text
    doc.add_heading("Dangerous goods", level=1)
    doc.add_paragraph("We can ship these only by road:", style=None)
    for item in [
        "Gas canisters (maximum 4 per order)",
        "Lithium batteries over 100 Wh",
        "Stove fuel in sealed bottles",
    ]:
        doc.add_paragraph(item, style="List Bullet")
    doc.add_heading("Returns by post", level=1)
    doc.add_paragraph(
        "Use the prepaid label from your account. Return parcels are processed within "
        "7 days of reaching our returns centre in Unit 9, Ridgeway Park."
    )
    doc.save(str(path))


def make_xlsx(path: Path) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    tents = wb.active
    tents.title = "Tents"
    tents.append(["SKU", "Name", "Capacity", "Weight (kg)", "Price", "In stock"])
    for row in [
        ("TN-2201", "Ridge Lite 2", 2, 1.4, 349.00, 18),
        ("TN-3305", "Basecamp Dome 3", 3, 3.9, 429.00, 7),
        ("TN-4410", "Family Cabin 6", 6, 11.2, 799.00, 3),
        ("TN-1107", "Solo Bivvy", 1, 0.9, 219.00, 25),
    ]:
        tents.append(row)
    bags = wb.create_sheet("Sleeping bags")
    bags.append(["SKU", "Name", "Comfort rating", "Fill", "Price", "In stock"])
    for row in [
        ("SB-0510", "Alpine Down -10", "-10 C", "800 fill down", 489.00, 9),
        ("SB-0205", "Summer Synthetic", "+5 C", "synthetic", 129.00, 41),
        ("SB-0715", "Expedition -25", "-25 C", "850 fill down", 899.00, 2),
    ]:
        bags.append(row)
    wb.save(str(path))


def make_pptx(path: Path) -> None:
    from pptx import Presentation

    prs = Presentation()
    slides = [
        (
            "Welcome to Summit Trail",
            ["Your first week", "Meet your buddy on day one", "Payroll runs on the 25th"],
        ),
        (
            "Your first week",
            [
                "Day 1: tills and returns",
                "Day 2: product training on tents",
                "Day 3: shadow a fitting session",
                "Day 5: check-in with the store manager",
            ],
        ),
        (
            "Systems you will use",
            [
                "Rota app: StaffHub",
                "Stock lookup: code STK",
                "Training portal: 12 modules, due within 30 days",
            ],
        ),
    ]
    for title, bullets in slides:
        slide = prs.slides.add_slide(prs.slide_layouts[1])
        slide.shapes.title.text = title
        body = slide.placeholders[1].text_frame
        body.text = bullets[0]
        for bullet in bullets[1:]:
            body.add_paragraph().text = bullet
    prs.save(str(path))


def make_scanned_pdf(path: Path) -> None:
    """An image-only PDF, like a photographed notice: nothing to extract without OCR."""
    from PIL import Image, ImageDraw, ImageFilter, ImageFont

    lines = [
        "NOTICE TO ALL STORE STAFF",
        "",
        "From 1 June the stockroom alarm code changes.",
        "The new code is 4471. Do not write it down.",
        "",
        "Deliveries now arrive on Tuesdays and Fridays",
        "between 7:00 and 9:00 am.",
        "",
        "The fire assembly point has moved to car park level P2.",
        "",
        "Questions: facilities desk, extension 2206.",
    ]
    image = Image.new("L", (1654, 2339), 248)  # A4 at 200 dpi, off-white paper
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype("arial.ttf", 44)
        heading = ImageFont.truetype("arialbd.ttf", 56)
    except OSError:
        font = heading = ImageFont.load_default()
    y = 220
    for i, line in enumerate(lines):
        draw.text((150, y), line, fill=30, font=heading if i == 0 else font)
        y += 90
    image = image.rotate(0.8, fillcolor=248).filter(ImageFilter.GaussianBlur(0.6))
    image.convert("RGB").save(str(path), "PDF", resolution=200.0)


def print_pdf(html: str, path: Path) -> None:
    browser = next((b for b in BROWSERS if Path(b).exists() or shutil.which(b)), None)
    if browser is None:
        sys.exit("No Chrome/Edge/Chromium found to print the handbook PDF")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "handbook.html"
        source.write_text(html, encoding="utf-8")
        subprocess.run(
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                "--no-pdf-header-footer",
                f"--print-to-pdf={path}",
                source.as_uri(),
            ],
            check=True,
            capture_output=True,
            timeout=120,
        )


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print_pdf(HANDBOOK_HTML, OUT / "employee-handbook.pdf")
    make_docx(OUT / "shipping-policy.docx")
    make_xlsx(OUT / "product-catalog.xlsx")
    make_pptx(OUT / "onboarding-slides.pptx")
    make_scanned_pdf(OUT / "store-notice-scanned.pdf")
    (OUT / "warranty-faq.html").write_text(FAQ_HTML, encoding="utf-8", newline="\n")
    (OUT / "release-notes.md").write_text(RELEASE_NOTES, encoding="utf-8", newline="\n")
    for f in sorted(OUT.iterdir()):
        print(f"{f.name:32} {f.stat().st_size:>8,} bytes")


if __name__ == "__main__":
    main()
