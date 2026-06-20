import base64, hashlib
from cryptography.fernet import Fernet
from sqlalchemy.orm import Session
from .models import AppSetting

SECRET_KEYS = {"openai_api_key","elevenlabs_api_key","twilio_account_sid","twilio_auth_token","mcp_auth_token"}
DEFAULTS = {
"openai_model":"gpt-4.1-mini","openai_temperature":"0.2","openai_max_turns":"20","agent_system_prompt":"You are a polite Nomos clearing voice agent.","extraction_prompt":"Extract structured clearing outcome JSON.",
"elevenlabs_stt_model":"scribe_v1","elevenlabs_de_voice_id":"","elevenlabs_en_voice_id":"","elevenlabs_tts_model":"eleven_multilingual_v2","speech_speed":"0.9","number_reading_mode":"digit_by_digit",
"twilio_phone_number":"","twilio_webhook_base_url":"","twilio_max_call_duration":"600","twilio_recording_enabled":"false",
"voice_safe_mode":"true","stt_enabled":"false","agent_enabled":"false","tts_enabled":"false",
"default_language":"de-DE","outbound_default_language":"de-DE","auto_detect_language":"true",
"ai_disclosure_required":"true","ai_disclosure_de":"Guten Tag, ich bin ein KI-Assistent von Nomos. Ich rufe wegen eines Klärfalls zur Anmeldung einer Marktlokation an.","ai_disclosure_en":"Hello, I am an AI assistant calling on behalf of Nomos about an energy market clearing case.","fake_data_only":"true",
"mcp_server_url":"","mcp_connection_id":"","mcp_sender_email":"","mcp_template_de":"Subject: Wir benötigen weitere Angaben zu Ihrem Stromanschluss\n\nGuten Tag {customer_name},\n\nwir haben die Anmeldung Ihres Stromanschlusses geprüft. Der Netzbetreiber hat uns mitgeteilt, dass die bisherige Marktlokation bzw. der Zähler nicht für die Anmeldung genutzt werden kann.\n\nGrund:\n{root_cause}\n\nBitte senden Sie uns die aktuellen Zähler- oder Anschlussdaten, damit wir die Anmeldung fortsetzen können.\n\nViele Grüße\nNomos","mcp_template_en":"Subject: We need additional information about your electricity connection\n\nHello {customer_name},\n\nwe checked the registration of your electricity connection. The grid operator informed us that the current market location or meter details cannot be used for the registration.\n\nReason:\n{root_cause}\n\nPlease send us the current meter or connection details so we can continue the registration.\n\nBest regards\nNomos","mcp_real_email_enabled":"false"}

def _fernet(key: str) -> Fernet:
    try: return Fernet(key.encode())
    except Exception:
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))

class SettingsService:
    def __init__(self, db: Session, encryption_key: str): self.db=db; self.fernet=_fernet(encryption_key or "dev-key")
    def get(self, key, default=None, reveal_secret=True):
        row=self.db.query(AppSetting).filter_by(key=key).first()
        if not row: return DEFAULTS.get(key, default)
        if row.is_secret:
            if not reveal_secret: return self.mask(row.encrypted_value or "")
            return self.fernet.decrypt(row.encrypted_value.encode()).decode() if row.encrypted_value else ""
        return row.value
    def set(self,key,value,is_secret=False):
        row=self.db.query(AppSetting).filter_by(key=key).first() or AppSetting(key=key,is_secret=is_secret)
        row.is_secret=is_secret
        if is_secret:
            if value and not str(value).startswith("••••"):
                row.encrypted_value=self.fernet.encrypt(value.encode()).decode(); row.value=None
        else: row.value=value; row.encrypted_value=None
        self.db.add(row); self.db.commit(); return row
    def all_masked(self): return {k:self.get(k, "", reveal_secret=(k not in SECRET_KEYS)) for k in set(DEFAULTS)|SECRET_KEYS}
    @staticmethod
    def mask(v): return "" if not v else "••••••" + v[-4:]
