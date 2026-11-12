"""Regenerate benchmarks/data/answers/items.jsonl (committed; run only to change the set).

    python -m benchmarks.answer_bench.build_items

The passages are the real benchmark documents (benchmarks/documents/make_fixtures.py: the
Summit Trail Outfitters handbook, shipping policy, catalogue, warranty FAQ, release notes,
onboarding slides and scanned notice; benchmarks/documents/furniture/: the Himalaya Gear Co
warranty pages and scraped site), cut the way the chunker cuts them (one heading section per
passage, tables kept whole as Markdown) and labelled with the source, section and page the
engine's context assembler prints. A few passages are translated into Hindi or Hinglish
(`translated: true`, `native_review: "pending"`) so the set also covers a knowledge base written
in the visitor's language; most Hindi and Hinglish questions are asked over English passages,
the engine's common case (documents_language = en).

Each item gets its gold passage(s) plus related distractors from the same business, 3 to 6 in
all, in a deterministic shuffled order: the gold passage is not always [1]. Unanswerable items
get passages near the topic that do NOT hold the answer, and forbidden facts the model might
borrow from them.
"""

import hashlib
import json
import random
import re
from dataclasses import dataclass, field
from typing import Any

from benchmarks.answer_bench import ITEMS_PATH
from benchmarks.answer_bench.items import Item, Passage

SUMMIT = "Summit Trail Outfitters"
HIMALAYA = "Himalaya Gear Co"
HB = "employee-handbook.pdf"
SP = "shipping-policy.docx"
PC = "product-catalog.xlsx"
WF = "warranty-faq.html"
RN = "release-notes.md"
OB = "onboarding-slides.pptx"
SN = "store-notice-scanned.pdf"
HT = "warranty-tents.html"
HBO = "warranty-boots.html"
HBA = "warranty-bags.html"

# Every amount in the set is Indian rupees, converted once here and used by every passage,
# translation, gold fact and forbidden value (all under one lakh, so "34,999" is unambiguous).
INR: dict[str, int] = {
    "uniform": 15_000,  # yearly uniform allowance
    "staff_cap": 75_000,  # yearly cap on the staff discount
    "per_km": 12,  # private-car mileage, per kilometre
    "hotel_other": 6_000,  # hotel cap per night outside metro cities
    "hotel_metro": 9_000,  # hotel cap per night inside metro cities
    "free_ship": 2_000,  # orders over this ship free within India
    "zone_a": 99,
    "zone_b": 149,
    "zone_c": 299,
    "ridge_lite": 14_999,
    "basecamp_dome": 18_499,
    "family_cabin": 34_999,
    "solo_bivvy": 8_999,
    "alpine_down": 21_999,
    "summer_synthetic": 5_499,
    "expedition": 38_999,
    "zip": 450,  # Himalaya repair list; Summit's repair desk starts at the same figure
    "seam": 650,
    "pole": 900,
    "resole": 1_800,  # one boot-resoling price for both businesses
    "down_refill": 2_400,
}


def amt(key: str) -> str:
    """`14,999` for a catalogue column: the number alone."""
    return f"{INR[key]:,}"


def rs(key: str) -> str:
    """`Rs 14,999` for prose."""
    return f"Rs {amt(key)}"


PRICE_TABLE = (
    "| Service | Price |\n|---|---|\n"
    f"| Zip replacement | {rs('zip')} |\n| Seam re-taping | {rs('seam')} |\n"
    f"| Pole section replacement | {rs('pole')} |\n| Boot resoling | {rs('resole')} |\n"
    f"| Down re-fill | {rs('down_refill')} |\n| Home pickup | Free within Pune |\n\n"
    "All prices are in Indian rupees and include GST. Offers are valid while stocks last, and "
    "the company may change terms without notice. E&OE."
)


@dataclass(frozen=True)
class P:
    id: str
    business: str
    file_name: str
    section: str | None
    page: int | None
    text: str
    language: str = "en"
    translation_of: str | None = None


POOL: list[P] = [
    # ---- Summit Trail Outfitters: employee handbook (PDF)
    P(
        "hb-intro",
        SUMMIT,
        HB,
        "Employee Handbook",
        1,
        "This handbook applies to every employee of Summit Trail Outfitters, in stores, in the "
        "warehouse and at head office. It is revised every January; this is revision 7.",
    ),
    P(
        "hb-hours-1",
        SUMMIT,
        HB,
        "Employee Handbook > Working hours",
        1,
        "Store staff work rotating shifts. A full-time shift is 8.5 hours including a 30-minute "
        "unpaid meal break. Shift rotas are published every Thursday for the following two weeks.",
    ),
    P(
        "hb-hours-2",
        SUMMIT,
        HB,
        "Employee Handbook > Working hours",
        1,
        "Warehouse staff start at 6:45 am. Anyone who clocks in more than 10 minutes late three "
        "times in a calendar month has a conversation with their team lead.",
    ),
    P(
        "hb-hours-3",
        SUMMIT,
        HB,
        "Employee Handbook > Working hours",
        1,
        "Head-office staff may work from home up to 2 days per week, agreed with their manager in "
        "advance. Core hours are 10:00 to 15:30.",
    ),
    P(
        "hb-hours-4",
        SUMMIT,
        HB,
        "Employee Handbook > Working hours",
        1,
        "Overtime is paid at 1.5 times the base rate on weekdays and 2 times the base rate on "
        "public holidays, and must be approved before it is worked.",
    ),
    P(
        "hb-leave-table",
        SUMMIT,
        HB,
        "Employee Handbook > Paid leave",
        1,
        "Annual leave grows with length of service:\n\n"
        "| Years of service | Annual leave days | Carry-over allowed |\n|---|---|---|\n"
        "| Less than 2 | 20 | 3 days |\n| 2 to 5 | 23 | 5 days |\n| More than 5 | 26 | 8 days |",
    ),
    P(
        "hb-leave-2",
        SUMMIT,
        HB,
        "Employee Handbook > Paid leave",
        1,
        "Parental leave is 18 weeks at full pay for the primary carer and 6 weeks for the "
        "secondary carer. Bereavement leave is 5 days for immediate family.",
    ),
    P(
        "hb-equip-1",
        SUMMIT,
        HB,
        "Employee Handbook > Equipment and the staff discount",
        2,
        f"Every new employee receives a uniform allowance of {rs('uniform')} per year. Employees "
        f"get a 35 percent discount on full-price gear and 15 percent on sale items, capped at "
        f"{rs('staff_cap')} of purchases a year. The discount code format is STAFF followed by "
        "the employee number.",
    ),
    P(
        "hb-equip-2",
        SUMMIT,
        HB,
        "Employee Handbook > Equipment and the staff discount",
        2,
        "Gear used for work, such as climbing ropes in the demo area, is inspected every 90 days "
        "and retired after 5 years regardless of condition.",
    ),
    P(
        "hb-safety-1",
        SUMMIT,
        HB,
        "Employee Handbook > Safety",
        2,
        "Forklifts in the warehouse may only be driven by staff holding a current licence, renewed "
        "every 3 years. The warehouse speed limit is 8 km/h. First-aid kits are checked on the "
        "first Monday of every month; the checker signs the log kept in kit 04.",
    ),
    P(
        "hb-safety-2",
        SUMMIT,
        HB,
        "Employee Handbook > Safety",
        2,
        "Report every incident, including near misses, within 24 hours using form HS-12.",
    ),
    P(
        "hb-expenses",
        SUMMIT,
        HB,
        "Employee Handbook > Expenses",
        2,
        f"Travel is reimbursed at {rs('per_km')} per kilometre for a private car. Hotel stays are "
        f"capped at {rs('hotel_other')} per night outside metro cities and {rs('hotel_metro')} "
        "inside them. Claims older than 60 days are not paid.",
    ),
    # ---- shipping policy (DOCX)
    P(
        "sp-intro",
        SUMMIT,
        SP,
        "Shipping policy",
        None,
        "Orders placed before 2 pm on a business day leave our warehouse the same day. Orders over "
        f"{rs('free_ship')} ship free within India.",
    ),
    P(
        "sp-zones",
        SUMMIT,
        SP,
        "Shipping policy > Delivery zones",
        None,
        "| Zone | Regions | Delivery time | Standard fee |\n|---|---|---|---|\n"
        f"| Zone A | Metro cities | 1-2 business days | {rs('zone_a')} |\n"
        f"| Zone B | Tier-2 and tier-3 towns | 3-4 business days | {rs('zone_b')} |\n"
        f"| Zone C | Remote areas and islands | 5-8 business days | {rs('zone_c')} |",
    ),
    P(
        "sp-dg",
        SUMMIT,
        SP,
        "Shipping policy > Dangerous goods",
        None,
        "We can ship these only by road:\n- Gas canisters (maximum 4 per order)\n"
        "- Lithium batteries over 100 Wh\n- Stove fuel in sealed bottles",
    ),
    P(
        "sp-returns",
        SUMMIT,
        SP,
        "Shipping policy > Returns by post",
        None,
        "Use the prepaid label from your account. Return parcels are processed within 7 days of "
        "reaching our returns centre in Unit 9, Okhla Industrial Estate, New Delhi.",
    ),
    # ---- product catalogue (XLSX)
    P(
        "pc-tents",
        SUMMIT,
        PC,
        "Tents",
        None,
        "| SKU | Name | Capacity | Weight (kg) | Price (Rs) | In stock |\n|---|---|---|---|---|---|\n"
        f"| TN-2201 | Ridge Lite 2 | 2 | 1.4 | {amt('ridge_lite')} | 18 |\n"
        f"| TN-3305 | Basecamp Dome 3 | 3 | 3.9 | {amt('basecamp_dome')} | 7 |\n"
        f"| TN-4410 | Family Cabin 6 | 6 | 11.2 | {amt('family_cabin')} | 3 |\n"
        f"| TN-1107 | Solo Bivvy | 1 | 0.9 | {amt('solo_bivvy')} | 25 |",
    ),
    P(
        "pc-bags",
        SUMMIT,
        PC,
        "Sleeping bags",
        None,
        "| SKU | Name | Comfort rating | Fill | Price (Rs) | In stock |\n|---|---|---|---|---|---|\n"
        f"| SB-0510 | Alpine Down -10 | -10 C | 800 fill down | {amt('alpine_down')} | 9 |\n"
        f"| SB-0205 | Summer Synthetic | +5 C | synthetic | {amt('summer_synthetic')} | 41 |\n"
        f"| SB-0715 | Expedition -25 | -25 C | 850 fill down | {amt('expedition')} | 2 |",
    ),
    # ---- warranty FAQ (HTML)
    P(
        "wf-tents",
        SUMMIT,
        WF,
        "Warranty questions > How long is the warranty on tents?",
        None,
        "Tents carry a 7-year warranty against manufacturing defects in the fabric, poles and zips. "
        "Damage from fire, UV exposure or pets is not covered.",
    ),
    P(
        "wf-bags",
        SUMMIT,
        WF,
        "Warranty questions > Are sleeping bags covered?",
        None,
        "Sleeping bags are covered for 3 years. Loss of loft from compressing a bag for long "
        "storage is normal wear and is not a defect.",
    ),
    P(
        "wf-boots",
        SUMMIT,
        WF,
        "Warranty questions > What about boots?",
        None,
        "Boots have a 12-month warranty on the sole bond and stitching. Resoling by our partner "
        f"workshop costs {rs('resole')} and restarts a 6-month workmanship guarantee.",
    ),
    P(
        "wf-claim",
        SUMMIT,
        WF,
        "Warranty questions > How do I make a claim?",
        None,
        "Bring the item and proof of purchase to any store, or start online with claim form W-3. "
        "Claims are assessed within 10 business days.",
    ),
    P(
        "wf-repair",
        SUMMIT,
        WF,
        "Warranty questions > Do you repair gear from other brands?",
        None,
        f"Yes. Our repair desk fixes zips, seams and punctures on any brand, from {rs('zip')}.",
    ),
    # ---- release notes (Markdown)
    P(
        "rn-420",
        SUMMIT,
        RN,
        "Store app release notes > Version 4.2.0",
        None,
        "Released on 14 March. Adds offline checkout: the till keeps working without a network for "
        "up to 72 hours and syncs sales when it reconnects.\n\n| Change | Ticket |\n|---|---|\n"
        "| Offline checkout | POS-1182 |\n| Faster barcode scan | POS-1190 |\n"
        "| Gift card balance on receipt | POS-1204 |",
    ),
    P(
        "rn-413",
        SUMMIT,
        RN,
        "Store app release notes > Version 4.1.3",
        None,
        "Fixes a rounding error on multi-buy discounts (ticket POS-1167) and raises the receipt "
        "printer timeout to 8 seconds.",
    ),
    P(
        "rn-410",
        SUMMIT,
        RN,
        "Store app release notes > Version 4.1.0",
        None,
        "Adds click-and-collect lockers. A locker reservation is held for 48 hours.",
    ),
    # ---- onboarding slides (PPTX)
    P(
        "ob-welcome",
        SUMMIT,
        OB,
        "Welcome to Summit Trail",
        1,
        "Your first week\nMeet your buddy on day one\nPayroll runs on the 25th",
    ),
    P(
        "ob-week",
        SUMMIT,
        OB,
        "Your first week",
        2,
        "Day 1: tills and returns\nDay 2: product training on tents\nDay 3: shadow a fitting "
        "session\nDay 5: check-in with the store manager",
    ),
    P(
        "ob-systems",
        SUMMIT,
        OB,
        "Systems you will use",
        3,
        "Rota app: StaffHub\nStock lookup: code STK\nTraining portal: 12 modules, due within 30 days",
    ),
    # ---- scanned notice (OCR)
    P(
        "sn-notice",
        SUMMIT,
        SN,
        None,
        1,
        "NOTICE TO ALL STORE STAFF\nFrom 1 June the stockroom alarm code changes. The new code is "
        "4471. Do not write it down.\nDeliveries now arrive on Tuesdays and Fridays between 7:00 "
        "and 9:00 am.\nThe fire assembly point has moved to parking level P2.\nQuestions: "
        "facilities desk, extension 2206.",
    ),
    # ---- Himalaya Gear Co: warranty pages and scraped site
    P(
        "hg-tent-lead",
        HIMALAYA,
        HT,
        "Tent warranty",
        None,
        "Every Himalaya tent is built in our Pune workshop and carries a written warranty against "
        "manufacturing defects.",
    ),
    P(
        "hg-tent-cover",
        HIMALAYA,
        HT,
        "Tent warranty > What is covered on a tent?",
        None,
        "Tents carry a 7-year warranty on the fabric, poles and zips against manufacturing defects. "
        "Damage from fire, UV exposure left by long storage in a car, or pets is not covered. The "
        "rain fly seam tape is covered for 2 years.",
    ),
    P(
        "hg-tent-claim",
        HIMALAYA,
        HT,
        "Tent warranty > How do I claim?",
        None,
        "Bring the tent and the invoice to any store, or fill claim form T-12 online. A tent claim "
        "is assessed within 7 business days and a pole section is posted free to any pin code in "
        "India.",
    ),
    P("hg-price-table", HIMALAYA, HT, "Tent warranty > Repair price list", None, PRICE_TABLE),
    P(
        "hg-boots-cover",
        HIMALAYA,
        HBO,
        "Boot warranty > What is covered on boots?",
        None,
        "Boots have a 12-month warranty on the sole bond and stitching. Worn lugs, scuffed leather "
        "and laces are normal wear and are not defects. Waterproof membranes are covered for 18 "
        "months if the boots were proofed with our own wax.",
    ),
    P(
        "hg-boots-resole",
        HIMALAYA,
        HBO,
        "Boot warranty > Can my boots be resoled?",
        None,
        "Yes. Our partner workshop in Dehradun resoles boots in 10 working days and restarts a "
        "6-month workmanship guarantee on the new sole.",
    ),
    P(
        "hg-bags-cover",
        HIMALAYA,
        HBA,
        "Sleeping bag warranty > What is covered on sleeping bags?",
        None,
        "Sleeping bags are covered for 3 years against baffle failures, broken zips and down "
        "leaking through the shell. Loss of loft from compressing a bag in its stuff sack for "
        "months is normal wear and is not a defect.",
    ),
    P(
        "hg-bags-loft",
        HIMALAYA,
        HBA,
        "Sleeping bag warranty > What if my bag has lost its loft?",
        None,
        "Send it to our Bengaluru service desk. A bag that is washed and re-filled with the "
        "original down gets a fresh 12-month guarantee on the new fill.",
    ),
    P(
        "hg-care-tent",
        HIMALAYA,
        "scrape-page-01.md",
        "Caring for a trekking tent",
        None,
        "Dry a tent completely before it goes back into its bag, even if that means pitching it in "
        "the terrace for an afternoon. A tent packed damp grows mildew within a week and the "
        "coating on the fly starts to peel. Wipe the poles, shake out the grit and store the tent "
        "loose in a cotton sack.",
    ),
    P(
        "hg-bag-ratings",
        HIMALAYA,
        "scrape-page-03.md",
        "Sleeping bag temperature ratings explained",
        None,
        "A comfort rating tells you the lowest temperature at which an average woman sleeps "
        "without shivering, while the limit rating is for an average man curled up in the cold. "
        "Buy by the comfort rating and add a liner if you sleep cold. The extreme rating is a "
        "survival figure, not a plan.",
    ),
    P(
        "hg-stove",
        HIMALAYA,
        "scrape-page-04.md",
        "Lighting and fuelling a camp stove",
        None,
        "Screw the burner onto a gas canister only when the valve is shut, and check the seal by "
        "brushing a little soapy water over the join. Bubbles mean a leak. Light the stove in the "
        "open, never inside a tent, and keep the canister out of direct sun.",
    ),
    # ---- translations written for this set (native review pending)
    P(
        "wf-tents-hi",
        SUMMIT,
        WF,
        "वारंटी के सवाल > टेंट पर वारंटी कितने समय की है?",
        None,
        "टेंट के कपड़े, पोल और ज़िप में निर्माण दोष पर 7 साल की वारंटी है। आग, धूप (UV) या पालतू "
        "जानवरों से हुआ नुकसान वारंटी में शामिल नहीं है।",
        "hi",
        "wf-tents",
    ),
    P(
        "sp-intro-hi",
        SUMMIT,
        SP,
        "शिपिंग नीति",
        None,
        "किसी कार्य-दिवस पर दोपहर 2 बजे से पहले दिए गए ऑर्डर उसी दिन हमारे गोदाम से निकल जाते हैं। "
        f"भारत के भीतर {INR['free_ship']:,} रुपये से ज़्यादा के ऑर्डर पर शिपिंग मुफ़्त है।",
        "hi",
        "sp-intro",
    ),
    P(
        "hb-equip-1-hi",
        SUMMIT,
        HB,
        "कर्मचारी हैंडबुक > उपकरण और स्टाफ़ छूट",
        2,
        f"हर नए कर्मचारी को हर साल {INR['uniform']:,} रुपये का यूनिफ़ॉर्म भत्ता मिलता है। कर्मचारियों को "
        "फुल-प्राइस सामान पर 35 प्रतिशत और सेल वाले सामान पर 15 प्रतिशत छूट मिलती है, जिसकी सीमा साल "
        f"में {INR['staff_cap']:,} रुपये की ख़रीद है। डिस्काउंट कोड का फ़ॉर्मैट है STAFF और उसके बाद "
        "कर्मचारी संख्या।",
        "hi",
        "hb-equip-1",
    ),
    P(
        "hg-price-table-hi",
        HIMALAYA,
        HT,
        "टेंट वारंटी > रिपेयर रेट सूची",
        None,
        f"| सेवा | कीमत |\n|---|---|\n| ज़िप बदलना | {rs('zip')} |\n| सीम री-टेपिंग | {rs('seam')} |\n"
        f"| पोल सेक्शन बदलना | {rs('pole')} |\n| बूट का सोल बदलना | {rs('resole')} |\n"
        f"| डाउन री-फ़िल | {rs('down_refill')} |\n| घर से पिकअप | पुणे में मुफ़्त |\n\n"
        "सभी कीमतें भारतीय रुपये में हैं और इनमें GST शामिल है।",
        "hi",
        "hg-price-table",
    ),
    P(
        "wf-claim-hi",
        SUMMIT,
        WF,
        "वारंटी के सवाल > क्लेम कैसे करें?",
        None,
        "सामान और ख़रीद का सबूत किसी भी स्टोर पर लाएँ, या क्लेम फ़ॉर्म W-3 से ऑनलाइन शुरू करें। क्लेम "
        "की जाँच 10 कार्य-दिवसों में की जाती है।",
        "hi",
        "wf-claim",
    ),
    P(
        "sp-returns-hi",
        SUMMIT,
        SP,
        "शिपिंग नीति > डाक से रिटर्न",
        None,
        "अपने अकाउंट से प्रीपेड लेबल इस्तेमाल करें। रिटर्न पार्सल यूनिट 9, ओखला इंडस्ट्रियल एस्टेट, "
        "नई दिल्ली स्थित हमारे रिटर्न सेंटर पहुँचने के 7 दिन के अंदर प्रोसेस किए जाते हैं।",
        "hi",
        "sp-returns",
    ),
    P(
        "hg-bags-loft-hi",
        HIMALAYA,
        HBA,
        "स्लीपिंग बैग वारंटी > अगर बैग का लॉफ्ट कम हो गया हो?",
        None,
        "अपना बैग हमारे बेंगलुरु सर्विस डेस्क पर भेजें। जिस बैग को धोकर ओरिजिनल डाउन से दोबारा भरा "
        "जाता है, उसे नई फ़िल पर 12 महीने की नई गारंटी मिलती है।",
        "hi",
        "hg-bags-loft",
    ),
    P(
        "hg-price-table-hl",
        HIMALAYA,
        HT,
        "Tent warranty > Repair rate list",
        None,
        f"| Service | Price |\n|---|---|\n| Zip replacement | {rs('zip')} |\n"
        f"| Seam re-taping | {rs('seam')} |\n| Pole section badalna | {rs('pole')} |\n"
        f"| Boot resoling | {rs('resole')} |\n| Down re-fill | {rs('down_refill')} |\n"
        "| Home pickup | Pune mein free |\n\n"
        "Sabhi prices Indian rupees mein hain aur GST included hai.",
        "hi-Latn",
        "hg-price-table",
    ),
    P(
        "hg-tent-claim-hl",
        HIMALAYA,
        HT,
        "Tent warranty > Claim kaise karein?",
        None,
        "Tent aur invoice kisi bhi store pe le aaiye, ya claim form T-12 online bhariye. Tent claim "
        "7 business days mein assess hota hai, aur pole section India ke kisi bhi pin code pe free "
        "bheja jaata hai.",
        "hi-Latn",
        "hg-tent-claim",
    ),
]
BY_ID = {p.id: p for p in POOL}


@dataclass(frozen=True)
class Q:
    id: str
    language: str
    type: str
    question: str
    gold: list[str]  # passage ids: holding the answer; for unanswerable, the related ones
    facts: list[list[str]] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)
    source: str = "authored"
    notes: str = ""
    # Checker hints. `attr`: lowercase keywords of the attribute the gold fact states (any
    # language); a passage outside the gold ones that mentions one with a different value of the
    # same kind (rupees, days, years, a form code...) is a conflict. `absent` (unanswerable only):
    # words that would mean a passage answers the question; none may appear. `bait` (unanswerable
    # only): forbidden values that DO appear in a nearby passage on purpose, to tempt borrowing.
    attr: list[str] = field(default_factory=list)
    absent: list[str] = field(default_factory=list)
    bait: list[str] = field(default_factory=list)


DQ = "benchmarks/documents/questions.jsonl"
FQ = "benchmarks/documents/furniture/questions.jsonl"
UQ = "benchmarks/documents/unanswerable.jsonl"

QUESTIONS: list[Q] = [
    # ======================= English, answerable (40)
    Q(
        "en-a-01",
        "en",
        "answerable",
        "How long is a full-time store shift?",
        ["hb-hours-1"],
        [["8.5 hours", "8.5-hour", "8.5 hrs"]],
        ["9 hours", "8 hours"],
        DQ,
    ),
    Q(
        "en-a-02",
        "en",
        "answerable",
        "When do the shift rotas come out?",
        ["hb-hours-1"],
        [["thursday"]],
        ["monday", "friday"],
        DQ,
    ),
    Q(
        "en-a-03",
        "en",
        "answerable",
        "What time do warehouse staff start?",
        ["hb-hours-2"],
        [["6:45"]],
        ["7:00", "6:30"],
        DQ,
    ),
    Q(
        "en-a-04",
        "en",
        "answerable",
        "How many days a week can head office staff work from home?",
        ["hb-hours-3"],
        [["2 days", "two days"]],
        ["3 days", "three days"],
        DQ,
    ),
    Q(
        "en-a-05",
        "en",
        "answerable",
        "What are the core hours at head office?",
        ["hb-hours-3"],
        [["10:00", "10 am", "10am"], ["15:30", "3:30"]],
        ["9:00", "17:00"],
        DQ,
    ),
    Q(
        "en-a-06",
        "en",
        "answerable",
        "What's the overtime rate on public holidays?",
        ["hb-hours-4"],
        [["2 times", "twice", "2x", "double"]],
        ["2.5 times", "3 times"],
        DQ,
    ),
    Q(
        "en-a-07",
        "en",
        "answerable",
        "How long is parental leave for the primary carer?",
        ["hb-leave-2"],
        [["18 weeks"]],
        ["16 weeks", "26 weeks"],
        DQ,
    ),
    Q(
        "en-a-08",
        "en",
        "answerable",
        "How many days of bereavement leave do I get?",
        ["hb-leave-2"],
        [["5 days", "five days"]],
        ["3 days", "7 days"],
        DQ,
    ),
    Q(
        "en-a-09",
        "en",
        "answerable",
        "What is the yearly uniform allowance?",
        ["hb-equip-1"],
        [["15,000", "15000"]],
        ["Rs 12,000", "Rs 18,000"],
        DQ,
        attr=["uniform", "यूनिफ़ॉर्म"],
    ),
    Q(
        "en-a-10",
        "en",
        "answerable",
        "What discount do staff get on full-price gear?",
        ["hb-equip-1"],
        [["35"]],
        ["25 percent", "40 percent"],
        DQ,
    ),
    Q(
        "en-a-11",
        "en",
        "answerable",
        "Is there a cap on how much I can buy with the staff discount?",
        ["hb-equip-1"],
        [["75,000", "75000"]],
        ["Rs 50,000", "Rs 90,000"],
        DQ,
    ),
    Q(
        "en-a-12",
        "en",
        "answerable",
        "How often are the climbing ropes in the demo area inspected?",
        ["hb-equip-2"],
        [["90 days"]],
        ["30 days", "60 days"],
        DQ,
    ),
    Q(
        "en-a-13",
        "en",
        "answerable",
        "How often does a forklift licence have to be renewed?",
        ["hb-safety-1"],
        [["3 years", "three years"]],
        ["2 years", "5 years"],
        DQ,
    ),
    Q(
        "en-a-14",
        "en",
        "answerable",
        "What's the speed limit in the warehouse?",
        ["hb-safety-1"],
        [["8 km/h", "8 kmph", "8 km per hour", "8 kilometres per hour"]],
        ["10 km/h", "5 km/h"],
        DQ,
    ),
    Q(
        "en-a-15",
        "en",
        "answerable",
        "Which form do I fill in to report a safety incident?",
        ["hb-safety-2"],
        [["hs-12"]],
        ["hs-10", "w-3"],
        DQ,
    ),
    Q(
        "en-a-16",
        "en",
        "answerable",
        "What mileage rate is paid if I drive my own car for work?",
        ["hb-expenses"],
        [["12"]],
        ["Rs 15", "Rs 10"],
        DQ,
        attr=["travel", "per kilometre"],
    ),
    Q(
        "en-a-17",
        "en",
        "answerable",
        "What's the hotel limit per night in a metro city?",
        ["hb-expenses"],
        [["9,000", "9000"]],
        ["Rs 8,000", "Rs 12,000"],
        DQ,
        attr=["hotel"],
    ),
    Q(
        "en-a-18",
        "en",
        "answerable",
        "How old can an expense claim be and still get paid?",
        ["hb-expenses"],
        [["60 days"]],
        ["30 days", "90 days"],
        DQ,
    ),
    Q(
        "en-a-19",
        "en",
        "answerable",
        "What's the cutoff time for same-day dispatch?",
        ["sp-intro"],
        [["2 pm", "2pm", "14:00", "2:00 pm"]],
        ["12 pm", "noon", "4 pm"],
        DQ,
    ),
    Q(
        "en-a-20",
        "en",
        "answerable",
        "Above what order value is shipping free?",
        ["sp-intro"],
        [["2,000", "2000"]],
        ["Rs 1,000", "Rs 1,500"],
        DQ,
        attr=["ship free", "free shipping"],
    ),
    Q(
        "en-a-21",
        "en",
        "answerable",
        "How many gas canisters can I order at once?",
        ["sp-dg"],
        [["4", "four"]],
        ["6 canisters", "2 canisters", "10 canisters"],
        DQ,
    ),
    Q(
        "en-a-22",
        "en",
        "answerable",
        "Where is your returns centre?",
        ["sp-returns"],
        [["unit 9"], ["okhla"]],
        ["unit 7", "unit 19"],
        DQ,
    ),
    Q(
        "en-a-23",
        "en",
        "answerable",
        "How quickly are postal returns processed?",
        ["sp-returns"],
        [["7 days", "seven days"]],
        ["14 days", "10 days"],
        DQ,
        attr=["returns centre", "रिटर्न सेंटर"],
    ),
    Q(
        "en-a-24",
        "en",
        "answerable",
        "How long does delivery to tier-2 and tier-3 towns take?",
        ["sp-zones"],
        [["3-4", "3 to 4", "three to four"]],
        ["2-3 business days", "4-5 business days"],
        DQ,
    ),
    Q(
        "en-a-25",
        "en",
        "answerable",
        "What's the standard fee to ship to remote areas and islands?",
        ["sp-zones"],
        [["299"]],
        ["Rs 249", "Rs 349"],
        DQ,
    ),
    Q(
        "en-a-26",
        "en",
        "answerable",
        "How much does the Basecamp Dome 3 tent cost?",
        ["pc-tents"],
        [["18,499", "18499"]],
        ["18,999", "19,499"],
        DQ,
    ),
    Q(
        "en-a-27",
        "en",
        "answerable",
        "How heavy is the Ridge Lite 2?",
        ["pc-tents"],
        [["1.4"]],
        ["1.6 kg", "2.4 kg"],
        DQ,
    ),
    Q(
        "en-a-28",
        "en",
        "answerable",
        "What is the SKU of the Solo Bivvy?",
        ["pc-tents"],
        [["tn-1107"]],
        ["tn-1170", "tn-1007"],
        DQ,
    ),
    Q(
        "en-a-29",
        "en",
        "answerable",
        "What's the comfort rating of the Expedition -25 bag?",
        ["pc-bags"],
        [["-25"]],
        ["-20 c", "-30 c"],
        DQ,
    ),
    Q(
        "en-a-30",
        "en",
        "answerable",
        "Which sleeping bag has synthetic fill and what does it cost?",
        ["pc-bags"],
        [["summer synthetic"], ["5,499", "5499"]],
        ["5,999", "4,999"],
        DQ,
    ),
    Q(
        "en-a-31",
        "en",
        "answerable",
        "How long is the warranty on your tents?",
        ["wf-tents"],
        [["7-year", "7 year", "seven-year", "7 years", "seven years"]],
        ["5-year", "10-year", "5 years", "10 years"],
        DQ,
        attr=["warranty"],
    ),
    Q(
        "en-a-32",
        "en",
        "answerable",
        "How much does it cost to resole boots?",
        ["wf-boots"],
        [["1,800", "1800"]],
        ["Rs 2,200", "Rs 1,500"],
        DQ,
        attr=["resol"],
    ),
    Q(
        "en-a-33",
        "en",
        "answerable",
        "Which form do I use to start a Summit Trail warranty claim online?",
        ["wf-claim"],
        [["w-3"]],
        ["w-2", "hs-12"],
        DQ,
    ),
    Q(
        "en-a-34",
        "en",
        "answerable",
        "How long does Summit Trail take to assess a warranty claim?",
        ["wf-claim"],
        [["10 business days", "10 working days", "ten business days"]],
        ["7 business days", "14 days"],
        DQ,
        attr=["assessed"],
    ),
    Q(
        "en-a-35",
        "en",
        "answerable",
        "Which release added offline checkout?",
        ["rn-420"],
        [["4.2.0"]],
        ["4.1.3", "4.1.0"],
        DQ,
    ),
    Q(
        "en-a-36",
        "en",
        "answerable",
        "How long can the till keep working without a network?",
        ["rn-420"],
        [["72 hours"]],
        ["48 hours", "24 hours"],
        DQ,
    ),
    Q(
        "en-a-37",
        "en",
        "answerable",
        "How long is a click-and-collect locker reservation held?",
        ["rn-410"],
        [["48 hours"]],
        ["72 hours", "24 hours"],
        DQ,
        attr=["locker"],
    ),
    Q(
        "en-a-38",
        "en",
        "answerable",
        "When does payroll run?",
        ["ob-welcome"],
        [["25th", "25"]],
        ["30th", "28th", "1st"],
        DQ,
    ),
    Q(
        "en-a-39",
        "en",
        "answerable",
        "What is the new stockroom alarm code?",
        ["sn-notice"],
        [["4471"]],
        ["4417", "1447"],
        DQ,
    ),
    Q(
        "en-a-40",
        "en",
        "answerable",
        "How much do you charge to replace a zip?",
        ["hg-price-table"],
        [["450"]],
        ["Rs 500", "Rs 400"],
        FQ,
        attr=["zip replacement"],
    ),
    # ======================= English, table (6)
    Q(
        "en-t-01",
        "en",
        "table",
        "Can you compare all your tents by capacity, weight and price?",
        ["pc-tents"],
        [
            ["ridge lite 2"],
            ["14,999", "14999"],
            ["basecamp dome 3"],
            ["18,499", "18499"],
            ["family cabin 6"],
            ["34,999", "34999"],
            ["solo bivvy"],
            ["8,999", "8999"],
        ],
        ["15,499", "18,999"],
    ),
    Q(
        "en-t-02",
        "en",
        "table",
        "Compare your sleeping bags: comfort rating, fill and price.",
        ["pc-bags"],
        [
            ["alpine down -10"],
            ["21,999", "21999"],
            ["summer synthetic"],
            ["5,499", "5499"],
            ["expedition -25"],
            ["38,999", "38999"],
        ],
        ["22,499", "5,999"],
    ),
    Q(
        "en-t-03",
        "en",
        "table",
        "What are the delivery times and fees for each zone?",
        ["sp-zones"],
        [["1-2", "1 to 2"], ["99"], ["3-4", "3 to 4"], ["149"], ["5-8", "5 to 8"], ["299"]],
        ["Rs 129", "Rs 199"],
    ),
    Q(
        "en-t-04",
        "en",
        "table",
        "How does annual leave change with years of service, including carry-over?",
        ["hb-leave-table"],
        [["20"], ["23"], ["26"], ["3 days"], ["5 days"], ["8 days"]],
        ["25 days", "30 days"],
    ),
    Q(
        "en-t-05",
        "en",
        "table",
        "Compare the warranty periods for tents, sleeping bags and boots.",
        ["wf-tents", "wf-bags", "wf-boots"],
        [
            ["7-year", "7 year", "7 years", "seven years"],
            ["3 years", "3-year", "three years"],
            ["12-month", "12 months", "12 month", "1 year", "one year"],
        ],
        ["5 years", "2 years", "24 months"],
    ),
    Q(
        "en-t-06",
        "en",
        "table",
        "What do your repairs cost? Please list the price of each service.",
        ["hg-price-table"],
        [["450"], ["650"], ["900"], ["1,800", "1800"], ["2,400", "2400"]],
        ["Rs 550", "Rs 1,500"],
    ),
    # ======================= English, unanswerable (8)
    Q(
        "en-u-01",
        "en",
        "unanswerable",
        "Is there a student discount?",
        ["hb-equip-1", "wf-repair"],
        [],
        ["35 percent", "35%", "15 percent", "15%"],
        UQ,
        "The staff discount is nearby and must not be offered as a student discount.",
        absent=["student"],
        bait=["35 percent", "15 percent"],
    ),
    Q(
        "en-u-02",
        "en",
        "unanswerable",
        "Do you offer no-cost EMI on credit cards?",
        ["sp-intro", "pc-tents"],
        [],
        [],
        UQ,
        absent=["emi", "instalment", "installment"],
    ),
    Q(
        "en-u-03",
        "en",
        "unanswerable",
        "How much does express next-day delivery cost?",
        ["sp-zones", "sp-intro"],
        [],
        ["99", "149", "299"],
        "authored",
        "Zone fees are standard delivery, not express.",
        absent=["express", "next-day", "next day"],
        bait=["99", "149", "299"],
    ),
    Q(
        "en-u-04",
        "en",
        "unanswerable",
        "What's the warranty on rucksacks?",
        ["wf-tents", "wf-bags", "wf-boots"],
        [],
        ["7-year", "7 years", "3 years", "12-month"],
        "authored",
        absent=["rucksack", "backpack"],
        bait=["7-year", "3 years", "12-month"],
    ),
    Q(
        "en-u-05",
        "en",
        "unanswerable",
        "How many sick days do employees get?",
        ["hb-leave-table", "hb-leave-2"],
        [],
        ["5 days", "20 days", "18 weeks"],
        "authored",
        absent=["sick"],
        bait=["5 days", "18 weeks"],
    ),
    Q(
        "en-u-06",
        "en",
        "unanswerable",
        "What time does the Pune store open on Sundays?",
        ["hg-tent-lead", "hg-tent-claim"],
        [],
        ["10:00", "10 am", "9 am"],
        "authored",
        absent=["sunday", "opening hours", "opens at"],
    ),
    Q(
        "en-u-07",
        "en",
        "unanswerable",
        "Do you deliver to Sri Lanka?",
        ["sp-zones", "sp-dg"],
        [],
        ["299", "5-8"],
        UQ,
        absent=["sri lanka", "international"],
        bait=["299", "5-8"],
    ),
    Q(
        "en-u-08",
        "en",
        "unanswerable",
        "What is the store's GST number?",
        ["hg-price-table", "hg-tent-claim"],
        [],
        [],
        UQ,
        absent=["gstin", "gst number", "gst no"],
    ),
    # ======================= Hindi (Devanagari), answerable (14)
    Q(
        "hi-a-01",
        "hi",
        "answerable",
        "फुल-टाइम स्टोर शिफ्ट कितने घंटे की होती है?",
        ["hb-hours-1"],
        [["8.5", "साढ़े आठ"]],
        ["9 घंटे", "9 hours"],
    ),
    Q(
        "hi-a-02",
        "hi",
        "answerable",
        "गोदाम के कर्मचारी सुबह कितने बजे काम शुरू करते हैं?",
        ["hb-hours-2"],
        [["6:45", "पौने सात"]],
        ["7:00", "6:30"],
    ),
    Q(
        "hi-a-03",
        "hi",
        "answerable",
        "टेंट पर कितने साल की वारंटी मिलती है?",
        ["wf-tents-hi"],
        [["7", "सात"]],
        ["5 साल", "10 साल"],
        notes="Hindi passage.",
        attr=["वारंटी"],
    ),
    Q(
        "hi-a-04",
        "hi",
        "answerable",
        "बूट का सोल दोबारा लगवाने में कितना खर्च आता है?",
        ["wf-boots"],
        [["1,800", "1800"]],
        ["2,200", "1,500"],
        attr=["resol", "सोल"],
    ),
    Q(
        "hi-a-05",
        "hi",
        "answerable",
        "सैलरी किस तारीख को आती है?",
        ["ob-welcome"],
        [["25", "25th"]],
        ["30 तारीख", "1 तारीख"],
    ),
    Q(
        "hi-a-06",
        "hi",
        "answerable",
        "कितने के ऑर्डर पर शिपिंग फ्री होती है?",
        ["sp-intro-hi"],
        [["2,000", "2000"]],
        ["1,000", "1,500"],
        notes="Hindi passage.",
        attr=["ship free", "शिपिंग"],
    ),
    Q(
        "hi-a-07",
        "hi",
        "answerable",
        "स्टाफ को फुल-प्राइस सामान पर कितनी छूट मिलती है?",
        ["hb-equip-1-hi"],
        [["35"]],
        ["25", "40"],
        notes="Hindi passage.",
    ),
    Q(
        "hi-a-08",
        "hi",
        "answerable",
        "Basecamp Dome 3 टेंट की कीमत क्या है?",
        ["pc-tents"],
        [["18,499", "18499"]],
        ["18,999", "19,499"],
    ),
    Q(
        "hi-a-09",
        "hi",
        "answerable",
        "ज़िप बदलवाने का कितना चार्ज लगता है?",
        ["hg-price-table-hi"],
        [["450"]],
        ["500", "400"],
        notes="Hindi passage.",
        attr=["zip", "ज़िप"],
    ),
    Q(
        "hi-a-10",
        "hi",
        "answerable",
        "Summit Trail में वारंटी क्लेम की जाँच में कितने दिन लगते हैं?",
        ["wf-claim-hi"],
        [["10", "दस"]],
        ["7 दिन", "14 दिन"],
        notes="Hindi passage.",
        attr=["assessed", "जाँच"],
    ),
    Q(
        "hi-a-11",
        "hi",
        "answerable",
        "स्टॉकरूम का नया अलार्म कोड क्या है?",
        ["sn-notice"],
        [["4471"]],
        ["4417", "1447"],
    ),
    Q(
        "hi-a-12",
        "hi",
        "answerable",
        "डाक से भेजे गए रिटर्न का सेंटर कहाँ है?",
        ["sp-returns-hi"],
        [["यूनिट 9", "unit 9"], ["ओखला", "okhla"]],
        ["यूनिट 7", "unit 7"],
        notes="Hindi passage.",
    ),
    Q(
        "hi-a-13",
        "hi",
        "answerable",
        "ऑफ़लाइन चेकआउट किस वर्ज़न में आया था?",
        ["rn-420"],
        [["4.2.0"]],
        ["4.1.3", "4.1.0"],
    ),
    Q(
        "hi-a-14",
        "hi",
        "answerable",
        "स्लीपिंग बैग का लॉफ्ट कम हो जाए तो उसे कहाँ भेजना है?",
        ["hg-bags-loft-hi"],
        [["बेंगलुरु", "bengaluru", "बैंगलोर", "bangalore"]],
        ["पुणे", "pune", "देहरादून", "dehradun"],
        notes="Hindi passage.",
    ),
    # ======================= Hindi, table (2)
    Q(
        "hi-t-01",
        "hi",
        "table",
        "अपने सभी टेंट की क्षमता, वज़न और कीमत की तुलना करके बताइए।",
        ["pc-tents"],
        [["14,999", "14999"], ["18,499", "18499"], ["34,999", "34999"], ["8,999", "8999"]],
        ["15,499", "18,999"],
    ),
    Q(
        "hi-t-02",
        "hi",
        "table",
        "रिपेयर की हर सर्विस का रेट एक टेबल में बता दीजिए।",
        ["hg-price-table-hi"],
        [["450"], ["650"], ["900"], ["1,800", "1800"], ["2,400", "2400"]],
        ["550", "1,500"],
        notes="Hindi passage.",
    ),
    # ======================= Hindi, unanswerable (4)
    Q(
        "hi-u-01",
        "hi",
        "unanswerable",
        "क्या छात्रों को कोई छूट मिलती है?",
        ["hb-equip-1"],
        [],
        ["35 प्रतिशत", "35%", "35 percent", "15 प्रतिशत"],
        absent=["student", "छात्र", "विद्यार्थी"],
        bait=["35 percent"],
    ),
    Q(
        "hi-u-02",
        "hi",
        "unanswerable",
        "एक्सप्रेस डिलीवरी का चार्ज कितना है?",
        ["sp-zones"],
        [],
        ["99", "149", "299"],
        absent=["express", "एक्सप्रेस", "next-day"],
        bait=["99", "149", "299"],
    ),
    Q(
        "hi-u-03",
        "hi",
        "unanswerable",
        "क्या आप पुराने टेंट एक्सचेंज में लेते हैं?",
        ["wf-tents", "pc-tents"],
        [],
        [],
        absent=["exchange", "एक्सचेंज", "trade-in", "trade in"],
    ),
    Q(
        "hi-u-04",
        "hi",
        "unanswerable",
        "रविवार को पुणे वाला स्टोर कितने बजे खुलता है?",
        ["hg-tent-lead", "hg-tent-claim"],
        [],
        ["10 बजे", "10:00", "9 बजे"],
        absent=["sunday", "रविवार", "opening hours"],
    ),
    # ======================= Hinglish (romanized), answerable (14)
    Q(
        "hl-a-01",
        "hi-Latn",
        "answerable",
        "Full time shift kitne ghante ki hoti hai?",
        ["hb-hours-1"],
        [["8.5", "saadhe aath", "sadhe aath"]],
        ["9 ghante", "9 hours"],
    ),
    Q(
        "hl-a-02",
        "hi-Latn",
        "answerable",
        "Rota kab publish hota hai?",
        ["hb-hours-1"],
        [["thursday", "guruvar"]],
        ["monday", "friday"],
    ),
    Q(
        "hl-a-03",
        "hi-Latn",
        "answerable",
        "Tent pe kitne saal ki warranty milti hai?",
        ["wf-tents"],
        [["7", "saat"]],
        ["5 saal", "10 saal"],
        attr=["warranty"],
    ),
    Q(
        "hl-a-04",
        "hi-Latn",
        "answerable",
        "Boot resole karwane ka kitna lagega?",
        ["wf-boots"],
        [["1,800", "1800"]],
        ["2,200", "1,500"],
        attr=["resol", "सोल"],
    ),
    Q(
        "hl-a-05",
        "hi-Latn",
        "answerable",
        "Free shipping kitne ke order pe milti hai?",
        ["sp-intro"],
        [["2,000", "2000"]],
        ["1,000", "1,500"],
        attr=["ship free"],
    ),
    Q(
        "hl-a-06",
        "hi-Latn",
        "answerable",
        "Zip replacement ka kitna charge hai bhai?",
        ["hg-price-table-hl"],
        [["450"]],
        ["500", "400"],
        notes="Hinglish passage.",
        attr=["zip"],
    ),
    Q(
        "hl-a-07",
        "hi-Latn",
        "answerable",
        "Ridge Lite 2 ka weight kitna hai?",
        ["pc-tents"],
        [["1.4"]],
        ["1.6", "2.4"],
    ),
    Q(
        "hl-a-08",
        "hi-Latn",
        "answerable",
        "Warehouse mein speed limit kya hai?",
        ["hb-safety-1"],
        [["8 km/h", "8 kmph", "8 km"]],
        ["10 km", "5 km"],
    ),
    Q(
        "hl-a-09",
        "hi-Latn",
        "answerable",
        "Safety incident report karne ke liye kaunsa form hai?",
        ["hb-safety-2"],
        [["hs-12"]],
        ["hs-10", "w-3"],
    ),
    Q(
        "hl-a-10",
        "hi-Latn",
        "answerable",
        "Home pickup free hai kya?",
        ["hg-price-table"],
        [["free", "muft", "free hai"], ["pune"]],
        ["Rs 200", "Rs 100"],
    ),
    Q(
        "hl-a-11",
        "hi-Latn",
        "answerable",
        "Summit Trail mein warranty claim online start karne ka form kaunsa hai?",
        ["wf-claim"],
        [["w-3"]],
        ["w-2", "t-12"],
    ),
    Q(
        "hl-a-12",
        "hi-Latn",
        "answerable",
        "Himalaya Gear mein tent claim kitne din mein assess hota hai?",
        ["hg-tent-claim-hl"],
        [["7"]],
        ["10 business days", "10 din", "10 days"],
        notes="Hinglish passage.",
        attr=["claim", "assessed"],
    ),
    Q(
        "hl-a-13",
        "hi-Latn",
        "answerable",
        "Locker reservation kitne ghante hold hota hai?",
        ["rn-410"],
        [["48"]],
        ["72 ghante", "72 hours", "24 hours"],
        attr=["locker"],
    ),
    Q(
        "hl-a-14",
        "hi-Latn",
        "answerable",
        "Deliveries kis din aati hain?",
        ["sn-notice"],
        [["tuesday", "tuesdays", "mangalvar"], ["friday", "fridays", "shukravar"]],
        ["monday", "somvar"],
    ),
    # ======================= Hinglish, table (2)
    Q(
        "hl-t-01",
        "hi-Latn",
        "table",
        "Har zone ka delivery time aur fee ek table mein dikha do.",
        ["sp-zones"],
        [["99"], ["149"], ["299"], ["1-2", "1 se 2"], ["3-4", "3 se 4"], ["5-8", "5 se 8"]],
        ["129", "199"],
    ),
    Q(
        "hl-t-02",
        "hi-Latn",
        "table",
        "Sleeping bags ka comparison chahiye: rating, fill aur price.",
        ["pc-bags"],
        [["21,999", "21999"], ["5,499", "5499"], ["38,999", "38999"]],
        ["22,499", "5,999"],
    ),
    # ======================= Hinglish, unanswerable (4)
    Q(
        "hl-u-01",
        "hi-Latn",
        "unanswerable",
        "Kya aap tent EMI pe dete ho?",
        ["hg-tent-cover", "hg-price-table"],
        [],
        [],
        absent=["emi", "instalment", "installment"],
    ),
    Q(
        "hl-u-02",
        "hi-Latn",
        "unanswerable",
        "Rucksack pe kitni warranty hai?",
        ["wf-tents", "wf-bags", "wf-boots"],
        [],
        ["7 saal", "7-year", "7 years", "3 saal"],
        absent=["rucksack", "backpack"],
        bait=["7-year"],
    ),
    Q(
        "hl-u-03",
        "hi-Latn",
        "unanswerable",
        "Sick leave kitne din ki milti hai?",
        ["hb-leave-table", "hb-leave-2"],
        [],
        ["5 din", "5 days", "20 din"],
        absent=["sick"],
        bait=["5 days"],
    ),
    Q(
        "hl-u-04",
        "hi-Latn",
        "unanswerable",
        "Mumbai store ka phone number kya hai?",
        ["hg-tent-lead", "hg-tent-claim"],
        [],
        [],
        absent=["mumbai", "phone", "telephone"],
    ),
]


def _seed(*parts: str) -> int:
    return int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if len(w) > 2}


def _business(q: Q) -> str:
    owners = {BY_ID[g].business for g in q.gold}
    if len(owners) != 1:
        raise ValueError(f"{q.id}: gold passages from more than one business")
    return owners.pop()


def _passages(q: Q) -> tuple[list[P], list[int]]:
    """Gold (or related) passages plus the most related distractors of the same business,
    3-6 in all, in a deterministic shuffled order."""
    gold = [BY_ID[g] for g in q.gold]
    business = _business(q)
    total = max(3 + _seed(q.id, "n") % 4, len(gold) + 2)
    total = min(total, 6)
    excluded = {p.id for p in gold} | {p.translation_of for p in gold if p.translation_of}
    probe = _words(q.question + " " + " ".join(p.text for p in gold))
    candidates = [
        p
        for p in POOL
        if p.business == business and p.id not in excluded and p.translation_of is None
    ]
    candidates.sort(
        key=lambda p: (
            -len(probe & _words(p.section or "") | probe & _words(p.text)),
            _seed(q.id, p.id),
        )
    )
    chosen = gold + candidates[: total - len(gold)]
    random.Random(_seed(q.id, "order")).shuffle(chosen)
    gold_idx = (
        sorted(i for i, p in enumerate(chosen, 1) if p.id in q.gold)
        if q.type != "unanswerable"
        else []
    )
    return chosen, gold_idx


# ------------------------------------------------------------------------------ consistency checker
#
# Every item must have exactly ONE correct answer derivable from its passages, in rupees. `audit()`
# returns every violation (empty = consistent); `build()` refuses to write a set that has any.
#
#  (a) every gold fact is stated by a gold passage of its item;
#  (b) no other passage of the item states a different value for the same attribute: a "measure"
#      (rupees, days, hours, weeks, months, years, per cent, km/h, or a form code like W-3) in a
#      sentence or table row that shares three or more content words with the gold sentence (or
#      carries one of the item's explicit `attr` keywords) must equal the gold measure of that
#      kind. A pool-wide pass does the same for the price list, warranty periods and the like, so
#      two businesses cannot quote different rupee prices for the same service either;
#  (c) an unanswerable item: none of its `absent` words (what would answer it) is in any passage,
#      and a forbidden value that is in a passage must be declared `bait` (nearby on purpose);
#  (d) every price is in rupees: no dollars or other currency anywhere, no price or fee column
#      without Rs, no bare amount next to a price word;
#  (e) a table item's facts are in its table (the Markdown rows), not just near it.

_FOREIGN_CURRENCY = re.compile(r"\$|\bdollars?\b|\busd\b|डॉलर|€|£|\beuros?\b|\bpounds?\b", re.I)
_PRICE_WORD = re.compile(
    r"\b(?:costs?|prices?|fees?|charges?|allowance|reimburs\w*)\b|कीमत|खर्च|चार्ज|भत्ता|शुल्क",
    re.I,
)
_PRICE_HEADER = re.compile(r"price|fee|cost|rate|कीमत|शुल्क|रेट", re.I)
_NUMBER = r"\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MEASURE = re.compile(
    rf"(?P<cur>(?:rs\.?|₹)\s*)?(?<![\w.,:])(?P<num>{_NUMBER})(?![\d:])"
    r"(?:\s*-?\s*(?:business\s+|working\s+|कार्य-)?(?P<unit>[A-Za-z%/]+|[\u0900-\u097F]+))?",
    re.I,
)
_CODE = re.compile(r"\b[A-Z]{1,3}-\d{1,4}\b")
_UNIT_KIND = {
    **dict.fromkeys(["rupees", "rupee", "rupaye", "रुपये", "रुपए", "रुपया"], "rs"),
    **dict.fromkeys(["day", "days", "din", "दिन", "दिनों", "दिवसों"], "day"),
    **dict.fromkeys(["hour", "hours", "hrs", "hr", "ghante", "ghanta", "घंटे", "घंटा"], "hour"),
    **dict.fromkeys(["week", "weeks", "hafte", "हफ्ते", "सप्ताह"], "week"),
    **dict.fromkeys(["month", "months", "mahine", "महीने", "महीनों"], "month"),
    **dict.fromkeys(["year", "years", "saal", "साल", "वर्ष"], "year"),
    **dict.fromkeys(["%", "percent", "प्रतिशत"], "pct"),
    **dict.fromkeys(["km/h", "kmph"], "kmh"),
}
_STOP = frozenset(
    [
        "that",
        "this",
        "with",
        "from",
        "have",
        "your",
        "they",
        "will",
        "into",
        "than",
        "each",
        "every",
        "their",
        "there",
        "which",
        "when",
        "where",
        "what",
        "does",
        "about",
        "after",
        "before",
        "within",
        "under",
        "over",
        "also",
        "only",
        "more",
        "most",
        "other",
        "such",
        "then",
        "them",
        "these",
        "those",
        "were",
        "been",
        "being",
        "would",
        "could",
        "should",
        "shall",
        "please",
        "business",
        "working",
        "days",
        "day",
        "hours",
        "hour",
        "weeks",
        "week",
        "months",
        "month",
        "years",
        "year",
        "percent",
        "rupees",
        "price",
        "prices",
        "note",
    ]
)

# Pool-wide attributes: (name, kind, [keyword groups]). A sentence or row that contains every word
# of any one group states this attribute; all its values of `kind` must agree across the POOL.
POOL_ATTRS: list[tuple[str, str, list[list[str]]]] = [
    ("boot resoling price", "rs", [["resoling"], ["resole"], ["सोल बदलना"]]),
    ("zip replacement price", "rs", [["zip replacement"], ["fixes zips"], ["ज़िप बदलना"]]),
    ("seam re-taping price", "rs", [["seam re-taping"], ["सीम री-टेपिंग"]]),
    ("pole section price", "rs", [["pole section"], ["पोल सेक्शन"]]),
    ("down re-fill price", "rs", [["down re-fill"], ["डाउन री-फ़िल"]]),
    ("free-shipping threshold", "rs", [["ship free"], ["शिपिंग मुफ़्त"]]),
    ("uniform allowance", "rs", [["uniform allowance"], ["यूनिफ़ॉर्म भत्ता"]]),
    ("tent warranty", "year", [["tent", "warranty"], ["टेंट", "वारंटी"]]),
    ("sleeping bag cover", "year", [["sleeping bags are covered"]]),
    ("boot warranty", "month", [["boots have a", "warranty"]]),
]


def _contains(text: str, form: str) -> bool:
    from benchmarks.answer_bench.score import contains

    return contains(text, form)


def _norm(text: str) -> str:
    from benchmarks.answer_bench.score import normalise

    return normalise(text)


def _units(text: str) -> list[str]:
    """Sentences and table rows. A cell under a "(Rs)" header gets its Rs, one under "(kg)" its
    kg, so a row reads like prose ("Basecamp Dome 3 | 3 | 3.9 kg | Rs 18,499")."""
    out: list[str] = []
    header: list[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line or re.fullmatch(r"[|\s:\-]+", line):
            continue
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip("|").split("|")]
            if not header:
                header = cells
                out.append(" | ".join(cells))
                continue
            row = []
            for i, cell in enumerate(cells):
                h = header[i] if i < len(header) else ""
                if re.search(r"\(rs\)", h, re.I) and re.fullmatch(_NUMBER, cell):
                    cell = f"Rs {cell}"
                elif re.search(r"\(kg\)", h, re.I) and re.fullmatch(_NUMBER, cell):
                    cell = f"{cell} kg"
                row.append(cell)
            out.append(" | ".join(row))
            continue
        header = []
        out += [x.strip() for x in re.split(r"(?<=[.!?।])\s+", line) if x.strip()]
    return out


def _measures(unit: str) -> list[tuple[str, float]]:
    """(kind, value) of every comparable quantity: rupees, durations, per cent, km/h, form codes."""
    found: list[tuple[str, float]] = []
    for m in _CODE.finditer(unit):
        found.append((f"code:{m.group(0)}", 0.0))
    body = _CODE.sub(" ", unit)
    for m in _MEASURE.finditer(body):
        value = float(m.group("num").replace(",", ""))
        unit_word = (m.group("unit") or "").lower()
        kind = "rs" if m.group("cur") else _UNIT_KIND.get(unit_word, "")
        if kind:
            found.append((kind, value))
    return found


def _stems(text: str) -> set[str]:
    words = re.findall(r"[^\W\d_]+", text.lower())
    return {w[:5] for w in words if len(w) >= 4 and w not in _STOP}


def _gold_units(q: Q, gold: list[P]) -> list[tuple[str, str, set[tuple[str, float]]]]:
    """(unit text, fact form, the unit's measures that the fact states) for each gold fact."""
    sources = [*gold, *(BY_ID[g.translation_of] for g in gold if g.translation_of)]
    out = []
    for fact in q.facts:
        for form in fact:
            numbers = {float(n.replace(",", "")) for n in re.findall(_NUMBER, _norm(form))}
            for p in sources:
                for unit in _units(p.text):
                    if not _contains(unit, form):
                        continue
                    stated = {
                        (k, v)
                        for k, v in _measures(unit)
                        if (k.startswith("code:") and k[5:].lower() in form.lower())
                        or (not k.startswith("code:") and v in numbers)
                    }
                    if stated:
                        out.append((unit, form, stated))
    return out


def _conflicts(q: Q, chosen: list[P]) -> list[str]:
    gold = [BY_ID[g] for g in q.gold]
    gold_ids = {p.id for p in gold}
    bad: list[str] = []
    for g_unit, form, stated in _gold_units(q, gold):
        g_stems = _stems(g_unit)
        attr_here = [a for a in q.attr if a.lower() in g_unit.lower()]
        for other in chosen:
            if other.id in gold_ids:
                continue
            for unit in _units(other.text):
                same = len(g_stems & _stems(unit)) >= 3 or any(
                    a.lower() in unit.lower() for a in attr_here
                )
                if not same:
                    continue
                theirs = _measures(unit)
                for kind in {k for k, _ in stated}:
                    mine = {v for k, v in stated if k == kind}
                    others = {v for k, v in theirs if k == kind}
                    if kind.startswith("code:"):
                        others = {1.0 for k, _ in theirs if k.startswith("code:") and k != kind}
                        mine = set() if others else {1.0}
                    if others and not (others & mine):
                        bad.append(
                            f"{q.id}: passage {other.id} states {sorted(others)} {kind} "
                            f"({unit!r}) but the gold fact {form!r} is {sorted(mine)} "
                            f"({g_unit!r})"
                        )
    return bad


def _currency(q: Q, chosen: list[P]) -> list[str]:
    bad: list[str] = []
    texts = {"question": q.question}
    texts.update({f"passage {p.id}": p.text + " " + (p.section or "") for p in chosen})
    texts["gold facts"] = " ".join(f for fact in q.facts for f in fact)
    texts["forbidden"] = " ".join(q.forbidden)
    for where, text in texts.items():
        if _FOREIGN_CURRENCY.search(text):
            bad.append(f"{q.id}: foreign currency in {where}: {text[:80]!r}")
    for p in chosen:
        lines = [ln for ln in p.text.split("\n") if ln.strip().startswith("|")]
        if lines:
            header = [c.strip() for c in lines[0].strip("|").split("|")]
            for col, name in enumerate(header):
                if not _PRICE_HEADER.search(name) or re.search(r"\(rs\)|rs|₹", name, re.I):
                    continue
                for row in lines[2:]:
                    cells = [c.strip() for c in row.strip("|").split("|")]
                    cell = cells[col] if col < len(cells) else ""
                    if re.fullmatch(_NUMBER, cell):
                        bad.append(
                            f"{q.id}: passage {p.id} column {name!r} has {cell!r} without Rs"
                        )
        for unit in _units(p.text):
            if _PRICE_WORD.search(unit) and not unit.startswith("|"):
                body = _CODE.sub(" ", unit)
                for m in _MEASURE.finditer(body):
                    plain = not m.group("cur") and not _UNIT_KIND.get(
                        (m.group("unit") or "").lower()
                    )
                    if plain and re.search(r"[.,]\d|\d{3,}", m.group("num")):
                        bad.append(f"{q.id}: passage {p.id} has a bare amount {m.group(0)!r}")
    return bad


def _unanswerable(q: Q, chosen: list[P]) -> list[str]:
    bad: list[str] = []
    blob = " ".join(p.text for p in chosen)
    for word in q.absent:
        if word.lower() in blob.lower():
            bad.append(f"{q.id}: passages mention {word!r}, which would answer the question")
    for wrong in q.forbidden:
        present = _contains(blob, wrong)
        if present and wrong not in q.bait:
            bad.append(f"{q.id}: forbidden {wrong!r} is in a passage and not declared bait")
    for wrong in q.bait:
        if wrong not in q.forbidden or not _contains(blob, wrong):
            bad.append(f"{q.id}: bait {wrong!r} is not a forbidden value present in a passage")
    if not q.absent:
        bad.append(f"{q.id}: an unanswerable item needs `absent` words")
    return bad


def _facts_and_tables(q: Q, chosen: list[P]) -> list[str]:
    bad: list[str] = []
    gold = [BY_ID[g] for g in q.gold]
    text = " ".join(p.text + " " + (p.section or "") for p in gold)
    table = "\n".join(ln for p in gold for ln in p.text.split("\n") if ln.strip().startswith("|"))
    for fact in q.facts:
        if not any(_contains(text, form) for form in fact):
            bad.append(f"{q.id}: gold fact {fact} is in no gold passage")
        elif q.type == "table" and table and not any(_contains(table, form) for form in fact):
            bad.append(f"{q.id}: table fact {fact} is not in the table rows")
    if q.type == "table" and not q.facts:
        bad.append(f"{q.id}: a table item has no gold facts")
    return bad


def check_item(q: Q, chosen: list[P]) -> list[str]:
    """Every violation for one item (see the rules above)."""
    bad = _currency(q, chosen)
    if q.type == "unanswerable":
        bad += _unanswerable(q, chosen)
    else:
        bad += _facts_and_tables(q, chosen) + _conflicts(q, chosen)
    return bad


def check_pool(pool: list[P] | None = None) -> list[str]:
    """Pool-wide: one value per attribute across every passage of every business."""
    bad: list[str] = []
    for name, kind, groups in POOL_ATTRS:
        seen: dict[float, str] = {}
        for p in pool if pool is not None else POOL:
            for unit in _units(p.text):
                low = unit.lower()
                if any(all(w.lower() in low for w in g) for g in groups):
                    for k, v in _measures(unit):
                        if k == kind:
                            seen.setdefault(v, p.id)
        if len(seen) > 1:
            bad.append(f"pool: {name} has more than one value: {seen}")
    return bad


def audit() -> list[str]:
    """Every violation over the whole set (empty = consistent)."""
    bad = check_pool()
    for q in QUESTIONS:
        chosen, _ = _passages(q)
        bad += check_item(q, chosen)
    return bad


def build() -> list[Item]:
    problems = check_pool()
    items = []
    for q in QUESTIONS:
        chosen, gold_idx = _passages(q)
        problems += check_item(q, chosen)
        passages = [
            Passage(
                id=p.id,
                file_name=p.file_name,
                section=p.section,
                page=p.page,
                text=p.text,
                language=p.language,  # type: ignore[arg-type]
                translated=p.translation_of is not None,
                translation_of=p.translation_of,
                native_review="pending" if p.translation_of else "n/a",
            )
            for p in chosen
        ]
        data: dict[str, Any] = {
            "id": q.id,
            "language": q.language,
            "type": q.type,
            "business": _business(q),
            "question": q.question,
            "passages": passages,
            "gold_passages": gold_idx,
            "gold_facts": q.facts,
            "forbidden_facts": q.forbidden,
            "source": q.source,
            "native_review": "pending" if q.language != "en" else "n/a",
            "notes": q.notes,
        }
        items.append(Item.model_validate(data))
    if problems:
        raise ValueError("inconsistent item set:\n" + "\n".join(problems))
    return items


def main() -> None:
    items = build()
    ITEMS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with ITEMS_PATH.open("w", encoding="utf-8", newline="\n") as fh:
        for item in items:
            fh.write(json.dumps(item.model_dump(), ensure_ascii=False) + "\n")
    counts: dict[tuple[str, str], int] = {}
    for item in items:
        counts[(item.language, item.type)] = counts.get((item.language, item.type), 0) + 1
    for key in sorted(counts):
        print(f"{key[0]:8} {key[1]:13} {counts[key]}")
    print(f"{len(items)} items -> {ITEMS_PATH}")


if __name__ == "__main__":
    main()
