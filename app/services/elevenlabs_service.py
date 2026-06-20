import base64, httpx
class ElevenLabsService:
    def __init__(self, settings): self.settings=settings
    async def test(self):
        key=self.settings.get('elevenlabs_api_key')
        if not key: return False,'Missing ElevenLabs API key'
        async with httpx.AsyncClient(timeout=10) as c:
            r=await c.get('https://api.elevenlabs.io/v1/user',headers={'xi-api-key':key}); return r.status_code<400, f'Status {r.status_code}'
    async def transcribe_chunk(self, audio: bytes):
        # TODO: replace chunk-buffer MVP with ElevenLabs streaming STT session for production latency.
        return None
    async def text_to_twilio_frames(self, text: str, language: str):
        # TODO: call ElevenLabs TTS and transcode to Twilio mulaw/8000. Empty frame keeps MVP non-blocking.
        return [base64.b64encode(b'').decode()]
