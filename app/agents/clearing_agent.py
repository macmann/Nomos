def fallback_response(case, transcript: str, language: str) -> str:
    if language == 'en-US':
        return f"Thank you. This is regarding {case.customer_address}. Could you please confirm the market location or meter status?"
    return f"Danke. Es geht um die Adresse {case.customer_address}. Können Sie bitte die Marktlokation oder den Zählerstatus bestätigen?"

class ClearingConversationAgent:
    """OpenAI Agents SDK-facing wrapper for the live clearing call tools and rules."""
