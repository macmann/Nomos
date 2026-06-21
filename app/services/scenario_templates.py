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
    "inactive_meter": {
        "label": "Meter no longer active",
        "case_type": "inactive_meter",
        "agent_goal": "Confirm whether the meter is inactive, removed, or was only temporary, and determine whether Nomos should contact the customer.",
        "problem_description": "The registration cannot proceed because the meter may no longer be active. The grid operator needs to confirm the meter status.",
        "required_outcome": "Confirm whether the meter is inactive or removed, capture the reason, and determine the next step.",
        "expected_fields": ["meter_status", "meter_inactive_reason", "registration_status", "reference_number", "next_action"],
        "success_conditions": ["meter_status is captured", "reason is captured if available", "next_action is known"],
        "next_action_options": ["notify_customer_by_email", "needs_manual_review", "retry_registration"],
    },
}
DEFAULT_SCENARIO = "correct_malo_id"

def normalize_scenario(value: str | None) -> str:
    return value if value in SCENARIOS else DEFAULT_SCENARIO

def get_scenario(value: str | None) -> dict:
    return SCENARIOS[normalize_scenario(value)]

def scenario_label(value: str | None) -> str:
    return get_scenario(value)["label"]
