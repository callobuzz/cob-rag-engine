"""The small fixed words of the home page's sections (LP2).

Like `ui.py` for the page chrome: labels, tags and captions around the deployer's own texts, in
every language the site can offer. A key missing in a language falls back to English. The
"Hindi" set is written by the builder and is listed for the owner's review in the epic notes.
"""

from typing import Final

_EN: Final[dict[str, str]] = {
    "tag_website": "Website",
    "tag_whatsapp": "WhatsApp",
    "ai_assistant": "AI assistant",
    "online": "online",
    "your_website": "Your website",
    "your_customer": "Your customer's phone",
    "checked_against": "Checked against your content",
    "caught": "Caught before it was sent",
    "sent_instead": "Sent instead",
    "inbox": "Lead inbox",
    "open_in_whatsapp": "Open in WhatsApp",
    "pause_video": "Pause video",
    "play_video": "Play video",
    "journey_eyebrow": "A customer's day",
    "journey_heading": "A question at night, a customer by lunch.",
    "journey_narrator": "Here is one customer's day, start to finish.",
    "journey_body": "The assistant answers at once, and the lead is waiting for you in the morning.",
    "email_subject": "New lead",
    "email_to_you": "To you",
    "ask_label": "Ask it yourself",
    "chapter": "Chapter",
    "estimated": "Estimated",
    "pinned_clock": "Time",
    "sample_note": "An example, not a real customer",
}

_HI: Final[dict[str, str]] = {
    "tag_website": "वेबसाइट",
    "tag_whatsapp": "व्हाट्सऐप",
    "ai_assistant": "एआई असिस्टेंट",
    "online": "ऑनलाइन",
    "your_website": "आपकी वेबसाइट",
    "your_customer": "आपके ग्राहक का फ़ोन",
    "checked_against": "आपकी सामग्री से जाँचा गया",
    "caught": "भेजने से पहले पकड़ा गया",
    "sent_instead": "इसके बदले भेजा गया",
    "inbox": "लीड इनबॉक्स",
    "open_in_whatsapp": "व्हाट्सऐप में खोलें",
    "pause_video": "वीडियो रोकें",
    "play_video": "वीडियो चलाएँ",
    "journey_eyebrow": "ग्राहक का एक दिन",
    "journey_heading": "रात को एक सवाल, दोपहर तक एक ग्राहक।",
    "journey_narrator": "एक ग्राहक का दिन, शुरू से आख़िर तक।",
    "journey_body": "असिस्टेंट तुरंत जवाब देता है, और सुबह लीड आपका इंतज़ार कर रहा होता है।",
    "email_subject": "नया लीड",
    "email_to_you": "आपके लिए",
    "ask_label": "खुद पूछकर देखें",
    "chapter": "अध्याय",
    "estimated": "अनुमानित",
    "pinned_clock": "समय",
    "sample_note": "यह एक उदाहरण है, असली ग्राहक नहीं",
}

_HI_LATN: Final[dict[str, str]] = {
    "tag_website": "Website",
    "tag_whatsapp": "WhatsApp",
    "ai_assistant": "AI assistant",
    "online": "online",
    "your_website": "Aapki website",
    "your_customer": "Aapke customer ka phone",
    "checked_against": "Aapki content se jaancha gaya",
    "caught": "Bhejne se pehle pakda gaya",
    "sent_instead": "Iske badle bheja gaya",
    "inbox": "Lead inbox",
    "open_in_whatsapp": "WhatsApp mein kholein",
    "pause_video": "Video rokein",
    "play_video": "Video chalayein",
    "journey_eyebrow": "Customer ka ek din",
    "journey_heading": "Raat ko ek sawaal, dopahar tak ek customer.",
    "journey_narrator": "Ek customer ka din, shuru se aakhir tak.",
    "journey_body": "Assistant turant jawab deta hai, aur subah lead aapka intezaar kar raha hota hai.",
    "email_subject": "Naya lead",
    "email_to_you": "Aapke liye",
    "ask_label": "Khud poochkar dekhein",
    "chapter": "Adhyaay",
    "estimated": "Anumaanit",
    "pinned_clock": "Samay",
    "sample_note": "Yeh ek example hai, asli customer nahi",
}

_BY_LANG: Final[dict[str, dict[str, str]]] = {"en": _EN, "hi": _HI, "hi-Latn": _HI_LATN}


def strings(lang: str) -> dict[str, str]:
    """The home page's fixed words for `lang`, English where one is missing."""
    return {**_EN, **_BY_LANG.get(lang, {})}
