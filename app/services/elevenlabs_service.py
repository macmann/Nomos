import logging

import httpx

from app.services.twilio_service import convert_audio_to_twilio_mulaw_8khz, convert_twilio_mulaw_to_wav_pcm16_16khz

logger = logging.getLogger(__name__)


def _normalize_stt_language(language_code: str | None) -> str | None:
    if not language_code:
        return None
    code = str(language_code).strip().lower().replace("_", "-")
    if not code:
        return None
    base = code.split("-", 1)[0]
    return base if base in {"en", "de", "fr", "es", "it", "pt", "pl", "hi"} else None


def _safe_response_text(response: httpx.Response, limit: int = 500) -> str:
    text = response.text or ""
    return text[:limit] + ("...[truncated]" if len(text) > limit else "")


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
        model_id = model_id or self.settings.get('elevenlabs_tts_model','eleven_flash_v2_5') or 'eleven_flash_v2_5'
        output_format = output_format or self.settings.get('tts_output_format','ulaw_8000') or 'ulaw_8000'
        url=f'https://api.elevenlabs.io/v1/text-to-speech/{voice_id}'
        params={'output_format': output_format}
        body={'text': text, 'model_id': model_id, 'voice_settings': {'stability': float(self.settings.get('elevenlabs_stability', '0.75') or 0.75), 'similarity_boost': float(self.settings.get('elevenlabs_similarity_boost', '0.75') or 0.75), 'style': float(self.settings.get('elevenlabs_style', '0.1') or 0.1), 'use_speaker_boost': str(self.settings.get('elevenlabs_use_speaker_boost', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}}}
        async with httpx.AsyncClient(timeout=30) as c:
            r=await c.post(url, params=params, json=body, headers={'xi-api-key':key})
            logger.warning('NOMOS_ELEVENLABS_TTS_STATUS status=%s bytes=%s format=%s model=%s', r.status_code, len(r.content or b''), output_format, model_id)
            if r.status_code >= 400 and model_id == 'eleven_flash_v2_5':
                fallback_model='eleven_multilingual_v2'
                r=await c.post(url, params=params, json={**body, 'model_id': fallback_model}, headers={'xi-api-key':key})
                logger.warning('NOMOS_ELEVENLABS_TTS_STATUS status=%s bytes=%s format=%s model=%s fallback_model=true', r.status_code, len(r.content or b''), output_format, fallback_model)
            if r.status_code >= 400 and output_format == 'ulaw_8000':
                fallback='pcm_16000'
                r=await c.post(url, params={'output_format': fallback}, json=body, headers={'xi-api-key':key})
                logger.warning('NOMOS_ELEVENLABS_TTS_STATUS status=%s bytes=%s format=%s', r.status_code, len(r.content or b''), fallback)
                r.raise_for_status(); return r.content, fallback
            r.raise_for_status(); return r.content, output_format

    async def synthesize_for_twilio(self, text: str, voice_id: str | None = None, model_id: str | None = None, output_format: str | None = None):
        try:
            audio, fmt = await self.synthesize(text, voice_id, model_id, output_format)
            twilio_audio = convert_audio_to_twilio_mulaw_8khz(audio, fmt)
            logger.warning('NOMOS_TTS_FORMAT format=ulaw_8000 raw=true bytes=%s', len(twilio_audio))
            return True, twilio_audio, 'ulaw_8000', f'TTS bytes {len(audio)}'
        except Exception as e:
            logger.exception('NOMOS_TTS_ERROR')
            return False, b'', output_format or 'ulaw_8000', str(e)

    async def transcribe_chunk(self, audio: bytes, language_code: str | None = None):
        key=self.settings.get('elevenlabs_api_key')
        if not key: return None
        logger.warning('NOMOS_STT_CONVERT input_mulaw_bytes=%s', len(audio or b''))
        wav = convert_twilio_mulaw_to_wav_pcm16_16khz(audio)
        logger.warning('NOMOS_STT_CONVERT output_wav_bytes=%s', len(wav or b''))
        model = self.settings.get('elevenlabs_stt_model','scribe_v1')
        normalized_language = _normalize_stt_language(language_code)
        if language_code and not normalized_language:
            logger.warning('NOMOS_STT_LANGUAGE_OMITTED unsupported_language=%s', language_code)
        async with httpx.AsyncClient(timeout=30) as c:
            files={'file': ('audio.wav', wav, 'audio/wav')}
            data={'model_id': model}
            if normalized_language:
                data['language_code'] = normalized_language
            r=await c.post('https://api.elevenlabs.io/v1/speech-to-text', headers={'xi-api-key':key}, data=data, files=files)
            logger.warning('NOMOS_ELEVENLABS_STT_STATUS status=%s bytes=%s language=%s', r.status_code, len(r.content or b''), normalized_language or 'auto')
            if r.status_code == 400 and normalized_language:
                logger.warning('NOMOS_ELEVENLABS_STT_RETRY_WITHOUT_LANGUAGE status=400 body=%s', _safe_response_text(r))
                files={'file': ('audio.wav', wav, 'audio/wav')}
                r=await c.post('https://api.elevenlabs.io/v1/speech-to-text', headers={'xi-api-key':key}, data={'model_id': model}, files=files)
                logger.warning('NOMOS_ELEVENLABS_STT_STATUS status=%s bytes=%s language=auto retry=true', r.status_code, len(r.content or b''))
            if r.status_code >= 400:
                logger.warning('NOMOS_ELEVENLABS_STT_ERROR status=%s body=%s', r.status_code, _safe_response_text(r))
                return None
            data=r.json()
            return (data.get('text') or '').strip()

    async def text_to_twilio_audio(self, text: str, language: str | None = None) -> bytes:
        voice = self.settings.get('elevenlabs_en_voice_id') if language == 'en-US' else self.settings.get('elevenlabs_de_voice_id')
        ok, audio, _fmt, msg = await self.synthesize_for_twilio(text, voice_id=voice)
        if not ok: raise RuntimeError(msg)
        return audio
