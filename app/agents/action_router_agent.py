from app.models import ActionRun
from app.services.mcp_mail_service import MCPMailService
class ActionRouterAgent:
    def __init__(self, db, settings): self.db=db; self.settings=settings
    async def run(self, case, call, extraction):
        action=extraction.next_action or 'none'
        if action=='update_malo' and extraction.corrected_market_location_number:
            case.market_location_number=extraction.corrected_market_location_number; status='success'
        elif action=='trigger_customer_email_agent':
            return await MCPMailService(self.settings,self.db).send_customer_email(case, extraction)
        elif action=='escalate_to_human': case.status='escalated'; status='success'
        elif action=='close_case': case.status='closed'; status='success'
        else: status='success'
        run=ActionRun(case_id=case.id, call_id=call.id if call else extraction.call_id, action_type=action, action_status=status, action_payload={'next_action':action})
        self.db.add(run); self.db.commit(); return run
