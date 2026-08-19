from __future__ import annotations

import unittest

from agentd.app.redaction import public_error


class PublicErrorTest(unittest.TestCase):
    def test_redacts_raw_and_masked_api_keys(self) -> None:
        value = public_error(
            "Authentication Fails, Your api key: ****1234 is invalid; sk-secretvalue"
        )

        self.assertNotIn("1234", value)
        self.assertNotIn("secretvalue", value)
        self.assertIn("api key: [REDACTED]", value)

    def test_redacts_bearer_header_and_bounds_output(self) -> None:
        value = public_error("Authorization: Bearer token-value " + "x" * 800)

        self.assertNotIn("token-value", value)
        self.assertLessEqual(len(value), 512)


if __name__ == "__main__":
    unittest.main()
