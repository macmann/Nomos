import httpx
from app.models import ActionRun
class MCPMailService:
    def __init__(self, settings, db): self.settings=settings; self.db=db
    async def test(self):
        url=self.settings.get('mcp_server_url'); return (bool(url), 'Configured' if url else 'Missing MCP server URL')
    async def send_customer_email(self, case, extraction):
        url=self.settings.get('mcp_server_url'); token=self.settings.get('mcp_auth_token'); real=self.settings.get('mcp_real_email_enabled')=='true'
        if not url:
            run=ActionRun(case_id=case.id, call_id=extraction.call_id, action_type='trigger_customer_email_agent', action_status='failed', error_message='MCP mail is not configured')
        else:
            payload={'to':case.customer_email,'from':self.settings.get('mcp_sender_email'),'dry_run':not real,'template_language':case.preferred_language,'root_cause':extraction.root_cause}
            try:
                async with httpx.AsyncClient(timeout=20) as c: resp=await c.post(url,json=payload,headers={'Authorization':'Bearer '+token} if token else {})
                run=ActionRun(case_id=case.id, call_id=extraction.call_id, action_type='trigger_customer_email_agent', action_status='success' if resp.status_code<400 else 'failed', action_payload={'status_code':resp.status_code,'dry_run':not real})
            except Exception as e: run=ActionRun(case_id=case.id, call_id=extraction.call_id, action_type='trigger_customer_email_agent', action_status='failed', error_message=str(e))
        self.db.add(run); self.db.commit(); return run
