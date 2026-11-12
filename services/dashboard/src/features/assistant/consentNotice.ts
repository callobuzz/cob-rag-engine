// The recommended consent wording (notice v2). Kept equal to _NOTICE in conversation/schema.py,
// pinned by tests/unit/test_consent_notice_v2.py.
export const RECOMMENDED_NOTICE: Record<"en" | "hi" | "hi-Latn", string> = {
	en: "**Before you share your details.** {business_name} will use your name and phone number (and e-mail, if you give it) for this purpose: {purpose} They are seen only by {business_name}'s team, stored securely, and deleted after {retention_days} days. To save you repeating yourself, a short summary of this chat and a reference code go to {business_name}'s team with your details. You can withdraw your consent and have them deleted at any time with the link we show after you submit, or by writing to {contact}. You can also complain to the Data Protection Board of India.{privacy_link}",
	hi: "**अपनी जानकारी देने से पहले।** {business_name} आपका नाम और फ़ोन नंबर (और ई-मेल, अगर आप दें) केवल इस उद्देश्य से उपयोग करेगा: {purpose} इन्हें केवल {business_name} की टीम देखती है, ये सुरक्षित रखे जाते हैं और {retention_days} दिनों के बाद मिटा दिए जाते हैं। आपको बार-बार बताना न पड़े, इसलिए इस चैट का एक छोटा सारांश और एक संदर्भ कोड आपकी जानकारी के साथ {business_name} की टीम को जाता है। आप जमा करने के बाद दिखने वाले लिंक से, या {contact} को लिखकर, कभी भी अपनी सहमति वापस ले सकते हैं और जानकारी मिटवा सकते हैं। आप भारत के डेटा संरक्षण बोर्ड से शिकायत भी कर सकते हैं।{privacy_link}",
	"hi-Latn":
		"**Apni details dene se pehle.** {business_name} aapka naam aur phone number (aur email, agar aap dein) sirf is liye use karega: {purpose} Inhe sirf {business_name} ki team dekhti hai, yeh surakshit rakhe jaate hain aur {retention_days} din ke baad delete ho jaate hain. Aapko baar-baar batana na pade, isliye is chat ka ek chhota summary aur ek reference code aapki details ke saath {business_name} ki team ko jaata hai. Submit karne ke baad dikhne wale link se, ya {contact} ko likh kar, aap kabhi bhi apni sahmati wapas le sakte hain aur details delete karwa sakte hain. Aap Data Protection Board of India se shikayat bhi kar sakte hain.{privacy_link}",
};

/** True when every language the owner has is the recommended wording. */
export function noticeIsRecommended(notice: Partial<Record<string, string>>): boolean {
	const entries = Object.entries(notice).filter(([, text]) => text);
	if (entries.length === 0) return true;
	return entries.every(([lang, text]) => RECOMMENDED_NOTICE[lang as keyof typeof RECOMMENDED_NOTICE] === text);
}
