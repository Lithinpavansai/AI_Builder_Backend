import asyncio
import unittest
from unittest.mock import MagicMock, patch
import groq

from app.utils import llm
from app.utils.config import config


class TestJSONRepair(unittest.TestCase):

    def setUp(self):
        llm.job_token_tracker.reset()

    def test_case_a_broken_stray_quotes_pattern(self):
        """Test (a): Array of 3 endpoint objects with stray quotes (e.g. ,\"{\" or [\"{\") -> parses, repair_count == 2."""
        # 3 endpoint objects where 2 have the stray quote before '{'
        broken_json = (
            '{\n'
            '  "api": {\n'
            '    "endpoints": [\n'
            '      {"path": "/products", "method": "GET"},\n'
            '      "{"path": "/products/{id}", "method": "GET"},\n'
            '      "{"path": "/products", "method": "POST"}\n'
            '    ]\n'
            '  }\n'
            '}'
        )
        parsed, repair_count = llm.repair_and_parse_json(broken_json)
        self.assertEqual(repair_count, 2)
        self.assertIn("api", parsed)
        self.assertEqual(len(parsed["api"]["endpoints"]), 3)
        self.assertEqual(parsed["api"]["endpoints"][1]["path"], "/products/{id}")
        self.assertEqual(parsed["api"]["endpoints"][2]["path"], "/products")

    def test_case_b_already_valid_json(self):
        """Test (b): Already-valid JSON -> unchanged, repair_count == 0."""
        valid_json = (
            '{\n'
            '  "app_name": "StoreApp",\n'
            '  "roles": ["Admin", "Customer"],\n'
            '  "api": {"endpoints": [{"path": "/health", "method": "GET"}]}\n'
            '}'
        )
        parsed, repair_count = llm.repair_and_parse_json(valid_json)
        self.assertEqual(repair_count, 0)
        self.assertEqual(parsed["app_name"], "StoreApp")
        self.assertEqual(parsed["roles"], ["Admin", "Customer"])

    def test_case_c_unrepairable_truncated_text(self):
        """Test (c): Unrepairable text (truncated mid-string) -> raises readable parse error, no crash."""
        truncated_text = '{"app_name": "IncompleteApp", "endpoints": [{"path": "/api/v1/users", "desc'
        with self.assertRaises(Exception) as ctx:
            llm.repair_and_parse_json(truncated_text)
        
        err = str(ctx.exception)
        self.assertNotIn("AttributeError", err)

    def test_case_d_failed_generation_none_in_400_body(self):
        """Test (d): failed_generation=None in a 400 body -> no AttributeError."""
        err_body = {
            "error": {
                "message": "JSON validate failed",
                "code": "json_validate_failed",
                "failed_generation": None,
            }
        }
        mock_400 = groq.BadRequestError(
            message="400 JSON validate failed",
            response=MagicMock(status_code=400, headers={}),
            body=err_body,
        )

        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = mock_400

        with patch.object(llm, "client", fake_client):
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(llm.call_llm_json_with_retry("test prompt", stage_name="Test Stage"))

            err = str(ctx.exception)
            self.assertNotIn("AttributeError", err)
            self.assertIn("json_validate_failed", err)


if __name__ == "__main__":
    unittest.main()
