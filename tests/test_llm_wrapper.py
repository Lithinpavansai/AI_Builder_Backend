import asyncio
import unittest
from unittest.mock import MagicMock, patch
import groq

from app.utils import llm
from app.utils.config import config


class FakeChoice:
    def __init__(self, content, finish_reason="stop"):
        self.message = MagicMock()
        self.message.content = content
        self.finish_reason = finish_reason


class FakeUsage:
    def __init__(self, prompt_tokens=100, completion_tokens=4000, reasoning_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = prompt_tokens + completion_tokens
        if reasoning_tokens > 0:
            self.completion_tokens_details = MagicMock()
            self.completion_tokens_details.reasoning_tokens = reasoning_tokens
        else:
            self.completion_tokens_details = None


class FakeResponse:
    def __init__(self, content, finish_reason="stop", prompt_tokens=100, completion_tokens=4000, reasoning_tokens=0, model="openai/gpt-oss-120b"):
        self.choices = [FakeChoice(content, finish_reason)]
        self.model = model
        self.usage = FakeUsage(prompt_tokens, completion_tokens, reasoning_tokens)


class TestLLMWrapper(unittest.TestCase):

    def setUp(self):
        # Reset tracker, budget, and limits
        llm.job_token_tracker.reset()
        llm.tpm_throttle.records.clear()
        llm.KNOWN_MODEL_OTPM_LIMITS.clear()

    def test_case_a_content_none_with_length_finish_reason(self):
        """Test (a): content=None with finish_reason=length raises readable error without AttributeError."""
        fake_response = FakeResponse(
            content=None,
            finish_reason="length",
            prompt_tokens=500,
            completion_tokens=4096,
            reasoning_tokens=3872,
            model="openai/gpt-oss-120b",
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response

        with patch.object(llm, "client", fake_client), \
             patch.object(config, "REASONING_EFFORT", "low"):
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(llm.call_llm_json_with_retry("test prompt", stage_name="Stage 3"))
            
            err = str(ctx.exception)
            self.assertNotIn("AttributeError", err)
            self.assertIn("Reasoning consumed the output budget", err)
            self.assertIn("reasoning_tokens=3872 of 4096", err)

    def test_case_b_json_validate_failed_with_none_failed_generation(self):
        """Test (b): 400 json_validate_failed with failed_generation=None raises readable error without AttributeError."""
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

    def test_case_c_valid_json_response_and_reasoning_effort_kwargs(self):
        """Test (c): Normal valid JSON parses, and reasoning_effort appears only for gpt-oss models."""
        valid_json = '{"app_name": "TestApp", "status": "ok"}'
        fake_response = FakeResponse(
            content=valid_json,
            finish_reason="stop",
            prompt_tokens=100,
            completion_tokens=50,
            model="openai/gpt-oss-120b",
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.return_value = fake_response

        # Test with gpt-oss model and REASONING_EFFORT="low"
        with patch.object(llm, "client", fake_client), \
             patch.object(config, "GROQ_MODEL", "openai/gpt-oss-120b"), \
             patch.object(config, "REASONING_EFFORT", "low"):
            
            res = asyncio.run(llm.call_llm_json_with_retry("test prompt", stage_name="Stage 1"))
            self.assertEqual(res, {"app_name": "TestApp", "status": "ok"})
            
            # Verify reasoning_effort was passed
            call_kwargs = fake_client.chat.completions.create.call_args[1]
            self.assertEqual(call_kwargs.get("reasoning_effort"), "low")

        # Test with non-gpt-oss model (e.g., llama or qwen) -> reasoning_effort should NOT be sent
        fake_client.reset_mock()
        with patch.object(llm, "client", fake_client), \
             patch.object(config, "GROQ_MODEL", "llama-3.3-70b-versatile"), \
             patch.object(config, "REASONING_EFFORT", "low"):
            
            res = asyncio.run(llm.call_llm_json_with_retry("test prompt", stage_name="Stage 1"))
            self.assertEqual(res, {"app_name": "TestApp", "status": "ok"})
            
            # Verify reasoning_effort was NOT passed
            call_kwargs = fake_client.chat.completions.create.call_args[1]
            self.assertNotIn("reasoning_effort", call_kwargs)

    def test_429_tpd_daily_limit_error(self):
        """Test 429 TPD body: maps to daily limit error without retry."""
        err_body = {
            "error": {
                "message": "Rate limit reached for model openai/gpt-oss-120b on tokens per day (TPD): Limit 100000, Used 100000. Try again in 2h30m.",
                "code": "rate_limit_exceeded",
            }
        }
        mock_429 = groq.RateLimitError(
            message="429 Rate Limit (TPD)",
            response=MagicMock(status_code=429, headers={}),
            body=err_body,
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = mock_429

        with patch.object(llm, "client", fake_client), \
             patch.object(config, "GROQ_MODEL", "openai/gpt-oss-120b"):
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(llm.call_llm("test prompt", stage_name="Stage 1"))

            err = str(ctx.exception)
            self.assertIn("Groq daily token limit reached for model openai/gpt-oss-120b", err)
            self.assertIn("Try again in 2h30m", err)
            # Assert no retries (call count == 1)
            self.assertEqual(fake_client.chat.completions.create.call_count, 1)

    def test_429_otpm_output_limit_error(self):
        """Test 429 OTPM body: parses Limit N, updates model limit, raises readable output error without retry or 413 confusion."""
        err_body = {
            "error": {
                "message": "Request too large for model openai/gpt-oss-120b on output tokens per minute (OTPM): Limit 1000, Requested 1091. Please try again in 5.4s.",
                "code": "rate_limit_exceeded",
            }
        }
        mock_429 = groq.RateLimitError(
            message="429 Rate Limit (OTPM)",
            response=MagicMock(status_code=429, headers={}),
            body=err_body,
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = mock_429

        with patch.object(llm, "client", fake_client), \
             patch.object(config, "GROQ_MODEL", "openai/gpt-oss-120b"):
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(llm.call_llm("test prompt", stage_name="Stage 3"))

            err = str(ctx.exception)
            self.assertNotIn("413", err)
            self.assertNotIn("payload too large", err)
            self.assertIn("Model openai/gpt-oss-120b allows ~1000 output tokens/min on this tier; stage Stage 3 needs more", err)
            # Limit 1000 was learned
            self.assertEqual(llm.KNOWN_MODEL_OTPM_LIMITS.get("openai/gpt-oss-120b"), 1000)
            # No retry loop (call count == 1)
            self.assertEqual(fake_client.chat.completions.create.call_count, 1)

    def test_429_tpm_retry_behavior(self):
        """Test 429 TPM body: waits and retries up to max 2 retries."""
        err_body = {
            "error": {
                "message": "Rate limit reached for model openai/gpt-oss-120b on tokens per minute (TPM): Limit 8000, Used 7500. Please try again in 0.01s.",
                "code": "rate_limit_exceeded",
            }
        }
        mock_429 = groq.RateLimitError(
            message="429 Rate Limit (TPM)",
            response=MagicMock(status_code=429, headers={"retry-after": "0.01"}),
            body=err_body,
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = mock_429

        with patch.object(llm, "client", fake_client), \
             patch.object(config, "GROQ_MODEL", "openai/gpt-oss-120b"):
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(llm.call_llm("test prompt", stage_name="Stage 1"))

            err = str(ctx.exception)
            self.assertIn("rate limit exceeded after 4 retries", err)
            # 1 initial + 4 retries = 5 calls
            self.assertEqual(fake_client.chat.completions.create.call_count, 5)

    def test_413_payload_too_large(self):
        """Test real 413 HTTP error: raises payload too large immediately without retry."""
        mock_413 = groq.APIStatusError(
            message="Payload Too Large",
            response=MagicMock(status_code=413, headers={}),
            body={"error": {"message": "Payload Too Large: request body exceeds 4MB limit", "code": "payload_too_large"}},
        )
        fake_client = MagicMock()
        fake_client.chat.completions.create.side_effect = mock_413

        with patch.object(llm, "client", fake_client), \
             patch.object(config, "GROQ_MODEL", "openai/gpt-oss-120b"):
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(llm.call_llm("test prompt", stage_name="Stage 1"))

            err = str(ctx.exception)
            self.assertIn("Groq API payload too large (413)", err)
            # No retries on 413
            self.assertEqual(fake_client.chat.completions.create.call_count, 1)

    def test_otpm_cap_below_stage_need(self):
        """Test that when OTPM is set (e.g. 1000) and stage requires 4096 tokens, it raises readable error without API call."""
        fake_client = MagicMock()
        with patch.object(llm, "client", fake_client), \
             patch.object(config, "GROQ_MODEL", "openai/gpt-oss-120b"), \
             patch.object(config, "GROQ_OTPM_LIMIT", 1000):
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(llm.call_llm("test prompt", max_tokens=4096, stage_name="Stage 3"))

            err = str(ctx.exception)
            self.assertIn("Model openai/gpt-oss-120b allows ~1000 output tokens/min on this tier; stage Stage 3 needs more", err)
            # 0 API calls made because it was caught up-front
            self.assertEqual(fake_client.chat.completions.create.call_count, 0)


if __name__ == "__main__":
    unittest.main()
