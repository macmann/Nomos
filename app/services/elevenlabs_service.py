import logging

import httpx

from app.services.twilio_service import convert_audio_to_twilio_mulaw_8khz, convert_twilio_mulaw_to_wav_pcm16k

logger = logging.getLogger(__name__)


class ElevenLabsService:
    def __init__(self, settings): self.settings=settings

    async def test(self):
        key=self.settings.get('elevenlabs_api_key')
        if not key: return False,'Missing ElevenLabs API key'
        output_format = self.settings.get('tts_output_format', 'ulaw_8000') or 'ulaw_8000'
        de_voice = self.settings.get('elevenlabs_de_voice_id') or self.settings.get('elevenlabs_en_voice_id')
        en_voice = self.settings.get('elevenlabs_en_voice_id') or de_voice
        parts=[]; success=True; total_bytes=0
        async with httpx.AsyncClient(timeout=20) as c:
            user=await c.get('https://api.elevenlabs.io/v1/user',headers={'xi-api-key':key})
            success = success and user.status_code < 400
            parts.append(f'API status {user.status_code}')
            for label, voice in [('German', de_voice), ('English', en_voice)]:
                if not voice:
                    success=False; parts.append(f'{label} voice missing'); continue
                ok, audio, fmt, msg = await self.synthesize_for_twilio('Test.', voice_id=voice, output_format=output_format)
                total_bytes += len(audio or b'')
                success = success and ok and bool(audio)
                parts.append(f'{label} voice {"ok" if ok else "failed"} ({msg}, format {fmt}, bytes {len(audio or b"")})')
        return success, '; '.join(parts) + f'; total bytes {total_bytes}'

    async def synthesize(self, text: str, voice_id: str | None = None, model_id: str | None = None, output_format: str | None = None) -> tuple[bytes, str]:
        key=self.settings.get('elevenlabs_api_key')
        if not key: raise RuntimeError('Missing ElevenLabs API key')
        voice_id = voice_id or self.settings.get('elevenlabs_de_voice_id') or self.settings.get('elevenlabs_en_voice_id')
        if not voice_id: raise RuntimeError('Missing ElevenLabs voice ID')
        model_id = model_id or self.settings.get('elevenlabs_tts_model','eleven_multilingual_v2')
        output_format = output_format or self.settings.get('tts_output_format','ulaw_8000') or 'ulaw_8000'
        url=f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}'
        params={'output_format': output_format}
        body={'text': text, 'model_id': model_id}
        async with httpx.AsyncClient(timeout=30) as c:
            r=await c.post(url, params=params, json=body, headers={'xi-api-key':key, 'Accept':'audio/mpeg'})
            logger.warning('NOMOS_ELEVENLABS_TTS_STATUS status=%s bytes=%s format=%s', r.status_code, len(r.content or b''), output_format)
            if r.status_code >= 400 and output_format == 'ulaw_8000':
                fallback='pcm_16000'
                r=await c.post(url, params={'output_format': fallback}, json=body, headers={'xi-api-key':key})
                logger.warning('NOMOS_ELEVENLABS_TTS_STATUS status=%s bytes=%s format=%s', r.status_code, len(r.content or b''), fallback)
                r.raise_for_status(); return r.content, fallback
            r.raise_for_status(); return r.content, output_format

    async def synthesize_for_twilio(self, text: str, voice_id: str | None = None, model_id: str | None = None, output_format: str | None = None):
        try:
            audio, fmt = await self.synthesize(text, voice_id, model_id, output_format)
            return True, convert_audio_to_twilio_mulaw_8khz(audio, fmt), 'ulaw_8000', f'TTS bytes {len(audio)}'
        except Exception as e:
            logger.exception('NOMOS_TTS_ERROR')
            return False, b'', output_format or 'ulaw_8000', str(e)

    async def transcribe_chunk(self, audio: bytes):
        key=self.settings.get('elevenlabs_api_key')
        if not key: return None
        wav = convert_twilio_mulaw_to_wav_pcm16k(audio)
        model = self.settings.get('elevenlabs_stt_model','scribe_v1')
        async with httpx.AsyncClient(timeout=30) as c:
            files={'file': ('twilio.wav', wav, 'audio/wav')}
            data={'model_id': model}
            r=await c.post('https://api.elevenlabs.io/v1/speech-to-text', headers={'xi-api-key':key}, data=data, files=files)
            logger.warning('NOMOS_ELEVENLABS_STT_STATUS status=%s bytes=%s', r.status_code, len(r.content or b''))
            r.raise_for_status()
            data=r.json()
            return (data.get('text') or '').strip()

    async def text_to_twilio_audio(self, text: str, language: str | None = None) -> bytes:
        voice = self.settings.get('elevenlabs_en_voice_id') if language == 'en-US' else self.settings.get('elevenlabs_de_voice_id')
        ok, audio, _fmt, msg = await self.synthesize_for_twilio(text, voice_id=voice)
        if not ok: raise RuntimeError(msg)
        return audio
