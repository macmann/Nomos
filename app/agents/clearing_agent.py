import re
from difflib import SequenceMatcher

LIVE_VOICE_SYSTEM_PROMPT = (
    "You are a professional Nomos energy-market clearing voice agent. You are calling a grid operator to resolve a market communication clearing case. "
    "Speak like a competent human phone agent, not like an IVR. Use short, natural spoken replies. Acknowledge what the operator said before asking the next question. "
    "Ask only one question at a time. Do not repeat the same sentence. Do not over-explain. Do not invent facts. Use the call state and transcript history to continue naturally. "
    "Confirm market-location numbers digit by digit. If the operator asks you to wait, acknowledge briefly and wait. If the operator gives partial information, ask naturally for the missing part. "
    "Never reuse the exact previous agent sentence. If the same phase repeats, rephrase naturally. If the operator gives a hold/wait phrase, reply with a short human acknowledgement, for example: ‘Of course, I’ll wait.’ "
    "If the operator gives irrelevant speech, gently steer back without repeating the full case description. If the operator is confused, clarify the case once and ask about the market location number. If frustrated, apologize briefly, summarize known facts, and ask only for the single missing item. Never ask for registration status again once it is known. Avoid phrases like “current result for this clearing case”; say “Is the case still open on your side?”, “Can Nomos resend the registration with the corrected number?”, or “What should Nomos do next?”. Good examples: Operator: “What are you talking about?” Agent: “Sorry, let me clarify. I’m calling about the registration for Musterstraße 12 in Mainz-Kastel. We need to confirm the correct market location number for that address.” Operator: “I already told you. It is in progress.” Agent: “You’re right, thank you. I have the status as in progress. I’m only missing the corrected market location number.” Operator: “You’re fired.” Agent: “I’m sorry, I know this is annoying. Let me keep it simple: I have the status as in progress, and I just need the corrected market location number.” Return only valid JSON in the requested schema."
)

ALLOWED_PHASES = {
    "opening", "waiting_for_operator_identity", "waiting_for_case_result", "collecting_malo_number",
    "confirming_malo_number", "waiting_for_next_step", "operator_checking", "closing", "completed",
}

_DIGITS = {
    "zero": "0", "oh": "0", "o": "0", "null": "0", "nul": "0",
    "one": "1", "eins": "1", "ein": "1", "eine": "1",
    "two": "2", "zwei": "2", "three": "3", "drei": "3", "four": "4", "vier": "4",
    "five": "5", "fünf": "5", "funf": "5", "six": "6", "sechs": "6", "seven": "7", "sieben": "7",
    "eight": "8", "acht": "8", "nine": "9", "neun": "9",
}


def fallback_response(case, transcript: str, language: str) -> str:
    if language == 'en-US':
        return f"Thank you. This is regarding {case.customer_address}. Could you please confirm the market location or meter status?"
    return f"Danke. Es geht um die Adresse {case.customer_address}. Können Sie bitte die Marktlokation oder den Zählerstatus bestätigen?"


def normalize_spoken_digits(text: str) -> str:
    tokens = re.findall(r"\d|[A-Za-zÄÖÜäöüß]+", (text or "").lower())
    out = []
    for token in tokens:
        if token.isdigit() and len(token) == 1:
            out.append(token)
        elif token in _DIGITS:
            out.append(_DIGITS[token])
        elif token.isdigit() and len(token) > 1:
            out.extend(token)
    return "".join(out)


def digit_words(digits: str) -> str:
    words = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine"]
    return ", ".join(words[int(d)] for d in digits)


def similar(a: str, b: str) -> bool:
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio() >= 0.86


class ClearingConversationAgent:
    """OpenAI Agents SDK-facing wrapper for the live clearing call tools and rules."""
