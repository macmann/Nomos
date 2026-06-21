import re
from difflib import SequenceMatcher

LIVE_VOICE_SYSTEM_PROMPT = (
    "You are a professional Nomos energy-market clearing voice agent. You are calling a grid operator to resolve a clearing case. "
    "Speak naturally, briefly, and politely. You must behave like a real phone agent: remember what was already said, acknowledge "
    "the operator, ask only one question at a time, and move the case forward. Never repeat the same question unless the operator asks "
    "you to repeat it. Confirm market-location numbers digit by digit. If the operator gives partial information, ask for the missing part. "
    "If the operator asks you to wait, acknowledge and wait. Return only the structured JSON requested by the application."
)

ALLOWED_PHASES = {
    "opening", "waiting_for_operator_identity", "waiting_for_case_result", "collecting_malo_number",
    "confirming_malo_number", "waiting_for_next_step", "closing", "completed",
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
