import os
import unittest
from unittest.mock import patch

from app.security import api_key_matches, configured_api_key


class ApiKeySecurityTests(unittest.TestCase):
    def test_missing_configuration_keeps_compatibility_mode(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(configured_api_key())
            self.assertTrue(api_key_matches(None))

    def test_missing_or_wrong_header_is_rejected_when_enabled(self):
        with patch.dict(os.environ, {"API_KEY": "expected-secret"}, clear=True):
            self.assertFalse(api_key_matches(None))
            self.assertFalse(api_key_matches("wrong-secret"))

    def test_exact_header_is_accepted_when_enabled(self):
        with patch.dict(os.environ, {"API_KEY": "expected-secret"}, clear=True):
            self.assertTrue(api_key_matches("expected-secret"))

    def test_blank_configuration_is_treated_as_disabled(self):
        with patch.dict(os.environ, {"API_KEY": "   "}, clear=True):
            self.assertIsNone(configured_api_key())
            self.assertTrue(api_key_matches("anything"))


if __name__ == "__main__":
    unittest.main()
