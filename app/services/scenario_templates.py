SCENARIOS = {
    "correct_malo_id": {
        "label": "Correct market location ID",
        "case_type": "malo_ident",
        "agent_goal": "Get the correct market location number from the grid operator and confirm it digit by digit.",
        "problem_description": "The market location number we have does not match the customer address. We need the grid operator to confirm the correct market location number.",
        "required_outcome": "Confirm the correct market location number, repeat it digit by digit, and identify whether Nomos can retry the registration.",
        "expected_fields": ["corrected_market_location_number", "registration_status", "reference_number", "next_action"],
        "success_conditions": ["corrected_market_location_number is captured", "operator confirms the number", "next_action is known"],
        "next_action_options": ["update_malo_and_retry_registration", "needs_manual_review"],
    },
    "meter_status_clarification": {
        "label": "Meter status clarification",
        "case_type": "meter_status_clarification",
        "agent_goal": "Confirm whether the meter is active, inactive, removed, or temporary. Based on the answer, determine whether Nomos can retry the registration or needs to contact the customer for updated meter details.",
        "problem_description": "The registration cannot proceed automatically because the meter status needs confirmation from the grid operator.",
        "required_outcome": "Confirm the meter status and determine the correct next step: retry registration if the meter is active, or contact the customer if the meter is inactive, removed, or only temporary.",
        "expected_fields": ["meter_status", "temporary_meter", "construction_meter", "meter_inactive_reason", "registration_can_continue", "customer_contact_required", "reference_number", "next_action"],
        "success_conditions": ["meter_status is captured", "registration continuation or customer contact need is known", "next_action is known"],
        "next_action_options": ["retry_registration", "notify_customer_by_email", "needs_manual_review"],
    },
}
DEFAULT_SCENARIO = "correct_malo_id"
LEGACY_SCENARIO_ALIASES = {"inactive_meter": "meter_status_clarification"}

def normalize_scenario(value: str | None) -> str:
    value = LEGACY_SCENARIO_ALIASES.get(value or "", value)
    return value if value in SCENARIOS else DEFAULT_SCENARIO

def get_scenario(value: str | None) -> dict:
    return SCENARIOS[normalize_scenario(value)]

def scenario_label(value: str | None) -> str:
    return get_scenario(value)["label"]
