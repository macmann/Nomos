import unittest
from unittest.mock import Mock, patch

from twilio.base.exceptions import TwilioRestException

from app.services.twilio_service import TwilioConfigurationError, TwilioService


class DictSettings:
    def __init__(self, values):
        self.values = values

    def get(self, key, default=None):
        return self.values.get(key, default)


class TwilioServiceTests(unittest.TestCase):
    def test_create_call_rejects_masked_credentials_before_api_call(self):
        service = TwilioService(DictSettings({
            "twilio_account_sid": "••••••1234",
            "twilio_auth_token": "••••••abcd",
            "twilio_phone_number": "+15551230000",
            "twilio_webhook_base_url": "https://example.com",
        }))

        with patch("app.services.twilio_service.Client") as client:
            with self.assertRaisesRegex(TwilioConfigurationError, "masked"):
                service.create_call("+15551239999", 30)
            client.assert_not_called()

    def test_create_call_turns_twilio_401_into_actionable_message(self):
        service = TwilioService(DictSettings({
            "twilio_account_sid": "AC123",
            "twilio_auth_token": "secret",
            "twilio_phone_number": "+15551230000",
            "twilio_webhook_base_url": "https://example.com/",
        }))
        mock_client = Mock()
        mock_client.calls.create.side_effect = TwilioRestException(
            status=401,
            uri="/Calls.json",
            msg="Unable to create record: Authenticate",
        )

        with patch("app.services.twilio_service.Client", return_value=mock_client):
            with self.assertRaisesRegex(RuntimeError, "Twilio authentication failed"):
                service.create_call("+15551239999", 30)


if __name__ == "__main__":
    unittest.main()
