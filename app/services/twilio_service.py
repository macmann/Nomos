from twilio.rest import Client
from twilio.twiml.voice_response import VoiceResponse, Connect, Stream
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
