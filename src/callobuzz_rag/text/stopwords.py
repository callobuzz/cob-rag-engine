"""Function words of Hindi (Devanagari) and of romanized Hindi ("Hinglish").

Hindi: postpositions, auxiliaries, pronouns, conjunctions and question particles, the
closed-class words that carry no topic. The list is a conservative subset of the widely used
open Hindi stop list (stopwords-iso/stopwords-hi, MIT licence), cut to words that are function
words in every use. Words that are also content (कर do, बात matter) were left out:
dropping a content word costs recall, keeping a function word costs only a little precision,
which BM25's IDF already discounts.

Hinglish: only forms that are not common English or product words ("par", "pe", "me", "ho",
"kar" are all left out; "to" and "the" are English stop words already).

Marathi, Bengali and the other Indic languages are not listed: their words are tokenized
whole and BM25's IDF handles the frequent ones. Add a list here to extend.
"""

HINDI_STOPWORDS: frozenset[str] = frozenset(
    """
    का की के को से में पर और या यह वह ये वो एक भी तो ही था थे थी थीं है हैं हो होता होती होते
    हूँ हूं हुआ हुई हुए गया गई गए गये रहा रही रहे कि कौन कब कहाँ कहां क्यों क्या कैसे कितना
    कितनी कितने जो जब जहाँ जहां तब तक सा सी से इस उस इन उन इसके उसके इसकी उसकी इसमें उसमें
    मैं मैंने हम हमें हमारा हमारी हमारे आप आपका आपकी आपके तुम तुम्हारा मेरा मेरी मेरे उसे इसे
    लिए लिये साथ द्वारा अपना अपनी अपने नहीं ना न वाला वाली वाले सकता सकती सकते चाहिए
    """.split()  # noqa: SIM905
)

HINGLISH_STOPWORDS: frozenset[str] = frozenset(
    """
    hai hain tha thi thhe hoga hogi honge ka ki ke ko mein se aur ya kya kyun kyon kaise kab
    kahan kaun kitna kitni kitne nahi nahin nhi bhi toh to ye yeh woh wo hum aap apka apki
    apke mera meri mere hamara hamari hamare ek isko usko iska uska iski uski isme usme liye
    """.split()  # noqa: SIM905
)

STOPWORDS_INDIC_AND_HINGLISH: frozenset[str] = HINDI_STOPWORDS | HINGLISH_STOPWORDS
