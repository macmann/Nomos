import json, httpx
class OpenAIAgentService:
    def __init__(self, settings): self.settings=settings
    async def test(self):
        key=self.settings.get('openai_api_key')
        if not key: return False,'Missing OpenAI API key'
        async with httpx.AsyncClient(timeout=10) as c:
            r=await c.get('https://api.openai.com/v1/models',headers={'Authorization':f'Bearer {key}'}); return r.status_code<400, f'Status {r.status_code}'
    async def respond(self, case, transcript, language):
        from app.agents.clearing_agent import fallback_response
        return fallback_response(case, transcript, language)
    async def extract(self, case, transcripts, events):
        text=' '.join(t.text for t in transcripts)
        return {"outcome":"unclear","root_cause": text[:500] or None,"market_location_number":case.market_location_number,"corrected_market_location_number":None,"meter_number":case.meter_number,"meter_status":"unknown","reference_number":None,"registration_status":"unknown","next_action":"none","plain_language_note":"MVP extraction generated from saved transcript.","confidence":0.5}
