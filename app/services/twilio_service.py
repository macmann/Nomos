import importlib
import importlib.util
import io
import wave

from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream

if importlib.util.find_spec("audioop") is not None:
    audioop = importlib.import_module("audioop")
else:
    from app import audioop_compat as audioop


def convert_audio_to_twilio_mulaw_8khz(audio_bytes: bytes, input_format: str) -> bytes:
    """Return raw 8 kHz mono μ-law bytes suitable for Twilio Media Streams."""
    fmt = (input_format or "").lower().strip()
    if not audio_bytes:
        return b""
    if fmt in {"ulaw_8000", "mulaw_8000", "ulaw", "mulaw"}:
        return audio_bytes
    if fmt in {"wav", "wave"}:
        with wave.open(io.BytesIO(audio_bytes), "rb") as wav:
            channels = wav.getnchannels()
            sample_width = wav.getsampwidth()
            frame_rate = wav.getframerate()
            pcm = wav.readframes(wav.getnframes())
        if channels > 1:
            pcm = audioop.tomono(pcm, sample_width, 0.5, 0.5)
        if frame_rate != 8000:
            pcm, _ = audioop.ratecv(pcm, sample_width, 1, frame_rate, 8000, None)
        return audioop.lin2ulaw(pcm, sample_width)
    if fmt in {"pcm_16000", "pcm_16k", "pcm_s16le_16000"}:
        sample_width = 2
        pcm, _ = audioop.ratecv(audio_bytes, sample_width, 1, 16000, 8000, None)
        return audioop.lin2ulaw(pcm, sample_width)
    if fmt in {"pcm_8000", "pcm_s16le_8000"}:
        return audioop.lin2ulaw(audio_bytes, 2)
    raise ValueError(f"Unsupported audio input_format for local Twilio conversion: {input_format}")


def convert_twilio_mulaw_to_wav_pcm16k(audio_bytes: bytes) -> bytes:
    pcm8 = audioop.ulaw2lin(audio_bytes, 2)
    pcm16, _ = audioop.ratecv(pcm8, 2, 1, 8000, 16000, None)
    out = io.BytesIO()
    with wave.open(out, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm16)
    return out.getvalue()


class TwilioService:
    def __init__(self, settings): self.settings=settings
    def test(self):
        sid=self.settings.get('twilio_account_sid'); token=self.settings.get('twilio_auth_token')
        if not sid or not token: return False,'Missing Twilio credentials'
        try: Client(sid,token).api.accounts(sid).fetch(); return True,'Twilio credentials valid'
        except Exception as e: return False, str(e)
    def create_call(self,to,call_id):
        sid=self.settings.get('twilio_account_sid'); token=self.settings.get('twilio_auth_token'); frm=self.settings.get('twilio_phone_number')
        base=(self.settings.get('twilio_webhook_base_url') or '').rstrip('/')
        cb=f'{base}/twilio/status'; url=f'{base}/twilio/voice/{call_id}'
        return Client(sid,token).calls.create(to=to, from_=frm, url=url, status_callback=cb, status_callback_event=['initiated','ringing','answered','completed'])
    @staticmethod
    def twiml(ws_url, call_id, case_id):
        r=VoiceResponse(); c=Connect(); s=Stream(url=ws_url); s.parameter(name='call_id', value=str(call_id)); s.parameter(name='case_id', value=str(case_id)); c.append(s); r.append(c); return str(r)
