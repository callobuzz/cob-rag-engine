"""The fixed sentences the WhatsApp channel says without a model (TV6): English, Hindi and
Hinglish, like the assistant's other fixed texts. Nothing here states a business fact."""

from typing import Final

LangTable = dict[str, str]

UNSUPPORTED: Final[LangTable] = {
    "en": "I can read text messages. Please type your question, or write 'talk to a person'.",
    "hi": "मैं टेक्स्ट संदेश पढ़ सकता हूँ। कृपया अपना सवाल लिखें, या 'किसी व्यक्ति से बात करनी है' लिखें।",
    "hi-Latn": "Main text message padh sakta hoon. Kripya apna sawaal likhiye, ya 'kisi insaan se "
    "baat karni hai' likhiye.",
}
STOPPED: Final[LangTable] = {
    "en": "Okay, we will not send you any more messages unless you write to us. Write START to "
    "hear from us again.",
    "hi": "ठीक है, जब तक आप हमें नहीं लिखते हम आपको और संदेश नहीं भेजेंगे। दोबारा संदेश पाने के लिए START लिखें।",
    "hi-Latn": "Theek hai, jab tak aap humein nahi likhte hum aapko aur message nahi bhejenge. "
    "Dobara message pane ke liye START likhiye.",
}
STARTED: Final[LangTable] = {
    "en": "Thank you. You may hear from us again. Write STOP any time to stop.",
    "hi": "धन्यवाद। हम आपको फिर से संदेश भेज सकते हैं। रोकने के लिए कभी भी STOP लिखें।",
    "hi-Latn": "Shukriya. Hum aapko phir se message bhej sakte hain. Rokne ke liye kabhi bhi STOP "
    "likhiye.",
}
OPT_IN_ASK: Final[LangTable] = {
    "en": "May we message you here if we have news about your request? You can say STOP any time.",
    "hi": "क्या आपके अनुरोध के बारे में कोई खबर होने पर हम आपको यहाँ संदेश भेज सकते हैं? आप कभी भी "
    "STOP लिख सकते हैं।",
    "hi-Latn": "Kya aapke request ke baare mein koi khabar hone par hum aapko yahan message bhej "
    "sakte hain? Aap kabhi bhi STOP likh sakte hain.",
}
OPT_IN_YES: Final[LangTable] = {"en": "Yes, message me", "hi": "हाँ, भेजें", "hi-Latn": "Haan, bhejein"}
OPT_IN_NO: Final[LangTable] = {"en": "No thanks", "hi": "नहीं, धन्यवाद", "hi-Latn": "Nahi, shukriya"}
OPT_IN_THANKS: Final[LangTable] = {
    "en": "Thank you, we will message you here if there is news.",
    "hi": "धन्यवाद, कोई खबर होने पर हम आपको यहाँ संदेश भेजेंगे।",
    "hi-Latn": "Shukriya, koi khabar hone par hum aapko yahan message bhejenge.",
}
OPT_IN_DECLINED: Final[LangTable] = {
    "en": "Okay, we will only reply when you write to us.",
    "hi": "ठीक है, हम केवल तभी जवाब देंगे जब आप हमें लिखेंगे।",
    "hi-Latn": "Theek hai, hum sirf tabhi jawab denge jab aap humein likhenge.",
}
NOT_AVAILABLE: Final[LangTable] = {
    "en": "This assistant is not available right now.",
    "hi": "यह सहायक अभी उपलब्ध नहीं है।",
    "hi-Latn": "Yeh assistant abhi available nahi hai.",
}
COULD_NOT_ANSWER: Final[LangTable] = {
    "en": "The assistant could not answer right now. Please try again in a little while.",
    "hi": "सहायक अभी जवाब नहीं दे सका। कृपया थोड़ी देर बाद फिर कोशिश करें।",
    "hi-Latn": "Assistant abhi jawab nahi de saka. Kripya thodi der baad phir koshish kijiye.",
}
BROCHURE_INTRO: Final[LangTable] = {
    "en": "Here you go.",
    "hi": "यह लीजिए।",
    "hi-Latn": "Yeh lijiye.",
}


def say(table: LangTable, lang: str) -> str:
    return table.get(lang) or table["en"]
