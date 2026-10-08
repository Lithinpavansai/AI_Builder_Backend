import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Optional
from groq import Groq
import groq
from dotenv import load_dotenv
from app.utils.config import config

load_dotenv()

client = Groq(api_key=os.getenv("GROQ_API_KEY"), max_retries=0)
logger = logging.getLogger("app_compiler")


def extract_balanced_json(text: Optional[str]) -> str:
    """Tolerant JSON extractor: strip code fences and extract the first balanced {...} block."""
    if text is None:
        return ""
    cleaned = (text or "").strip()
    if not cleaned:
        return ""

    # Strip markdown code fences if present
    if "```json" in cleaned:
        parts = cleaned.split("```json")
        if len(parts) > 1:
            after_json = parts[1]
            if "```" in after_json:
                cleaned = after_json.split("```")[0].strip()
    elif "```" in cleaned:
        parts = cleaned.split("```")
        for part in parts:
            part_str = part.strip()
            if part_str.startswith("{") and part_str.endswith("}"):
                cleaned = part_str
                break
    else:
        if cleaned.startswith("```json"):
            cleaned = cleaned[7:]
        elif cleaned.startswith("```"):
            cleaned = cleaned[3:]
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]

    cleaned = cleaned.strip()

    start = cleaned.find("{")
    if start == -1:
        return cleaned

    depth = 0
    in_string = False
    escape = False

    for i in range(start, len(cleaned)):
        ch = cleaned[i]
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if not in_string:
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return cleaned[start : i + 1]

    # Fallback to substring if unbalanced
    end = cleaned.rfind("}")
    if end > start:
        return cleaned[start : end + 1]

    return cleaned[start:]


class JobTokenTracker:
    def __init__(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.total_calls = 0
        self.total_sleep_seconds = 0.0
        self.total_repair_count = 0
        self.total_normalizations = 0
        self.stage_3_finish_reasons: dict[str, str] = {}

    def reset(self):
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.total_calls = 0
        self.total_sleep_seconds = 0.0
        self.total_repair_count = 0
        self.total_normalizations = 0
        self.stage_3_finish_reasons = {}

    def add_usage(self, usage):
        if usage:
            self.prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "completion_tokens", 0) or 0
            self.total_tokens += getattr(usage, "total_tokens", 0) or 0
        self.total_calls += 1

    def add_sleep(self, seconds: float):
        self.total_sleep_seconds += seconds

    def add_repairs(self, count: int):
        self.total_repair_count += count

    def add_normalizations(self, count: int = 1):
        self.total_normalizations += count

    def record_stage_3_finish_reason(self, part: str, finish_reason: str):
        self.stage_3_finish_reasons[part] = finish_reason

    def to_dict(self):
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "total_calls": self.total_calls,
            "total_sleep_seconds": round(self.total_sleep_seconds, 2),
            "total_repair_count": self.total_repair_count,
            "total_normalizations": self.total_normalizations,
            "stage_3_finish_reasons": self.stage_3_finish_reasons,
        }


job_token_tracker = JobTokenTracker()


def normalize_part(parsed: Any, part: str) -> dict:
    """
    Normalizes Call A or Call B response.
    Handles dict or list-shapes (e.g. [{"ui":...}, "auth", ":", {...}]).
    Filters allowed keys and validates required keys.
    """
    raw_dict: dict = {}
    shape = "dict" if isinstance(parsed, dict) else ("list" if isinstance(parsed, list) else "other")

    if isinstance(parsed, dict):
        raw_dict = dict(parsed)
    elif isinstance(parsed, list):
        shape = "list"
        i = 0
        while i < len(parsed):
            item = parsed[i]
            if isinstance(item, dict):
                for k, v in item.items():
                    if v or k not in raw_dict:
                        raw_dict[k] = v
                i += 1
            elif isinstance(item, str):
                key = item.strip()
                if key == ":":
                    i += 1
                    continue
                # Next element (skipping any ":") is value
                val = None
                j = i + 1
                while j < len(parsed) and isinstance(parsed[j], str) and parsed[j].strip() == ":":
                    j += 1
                if j < len(parsed):
                    val = parsed[j]
                    i = j + 1
                else:
                    i += 1

                if val is not None:
                    if val or key not in raw_dict:
                        raw_dict[key] = val
            else:
                i += 1
    else:
        raise ValueError(f"Schema Generation part {part} returned unexpected type {type(parsed).__name__}")

    part_key = part.upper()
    if "A" in part_key:
        allowed_keys = {"ui", "api"}
        required_keys = {"ui", "api"}
    elif "B" in part_key:
        # Call B keeps only database, auth, business_logic (and any other top-level key EXCEPT ui/api)
        allowed_keys = {k for k in raw_dict.keys() if k not in ("ui", "api")}
        allowed_keys.update({"database", "auth", "business_logic"})
        required_keys = {"database", "auth"}
    else:
        allowed_keys = set(raw_dict.keys())
        required_keys = set()

    kept_dict = {}
    dropped_keys = []
    for k, v in raw_dict.items():
        if k in allowed_keys:
            kept_dict[k] = v
        else:
            dropped_keys.append(k)

    print(f"[PART {part.upper()} NORMALIZE] shape={shape}, dropped keys: {dropped_keys}, kept keys: {list(kept_dict.keys())}", flush=True)

    # Check required keys
    missing = [k for k in required_keys if k not in kept_dict or kept_dict[k] is None]
    if missing:
        raise ValueError(f"Schema Generation part {part} returned keys {list(raw_dict.keys())}; missing {missing}")

    if shape == "list" or dropped_keys:
        job_token_tracker.add_normalizations(1)

    return kept_dict


def repair_and_parse_json(text: Optional[str]) -> tuple[dict, int]:
    """
    Tolerantly extracts JSON, cleans known LLM formatting glitches (e.g., stray quotes
    before '{' inside arrays), and parses to a dict.
    Returns (parsed_dict, repair_count).
    """
    if text is None:
        raise ValueError("Cannot parse JSON from None")

    extracted = extract_balanced_json(text)
    if not extracted:
        raise ValueError("Model returned empty content")

    # Step 1: Direct JSON parsing
    try:
        return json.loads(extracted), 0
    except json.JSONDecodeError:
        pass

    repairs = 0
    repaired_str = extracted

    # Step 2: Regex replacements: ,"{" -> ,{" and ["{" -> [{"
    new_str, n1 = re.subn(r'(,\s*)"(\{)', r'\1\2', repaired_str)
    new_str, n2 = re.subn(r'(\[\s*)"(\{)', r'\1\2', new_str)
    repairs += (n1 + n2)
    repaired_str = new_str

    try:
        parsed = json.loads(repaired_str)
        if repairs > 0:
            print(f"[JSON REPAIR] removed {repairs} stray quotes")
            job_token_tracker.add_repairs(repairs)
        return parsed, repairs
    except json.JSONDecodeError as err:
        last_err = err

    # Step 3: Position-based repair loop (max 10 iterations)
    for _ in range(10):
        pos = getattr(last_err, "pos", None)
        if pos is None:
            break

        fixed = False
        start_search = max(0, pos - 5)
        end_search = min(len(repaired_str), pos + 6)
        window = repaired_str[start_search:end_search]

        # Look for stray quote right before { or [
        match = re.search(r'"([\{\[])', window)
        if match:
            quote_idx = start_search + match.start()
            repaired_str = repaired_str[:quote_idx] + repaired_str[quote_idx + 1 :]
            repairs += 1
            fixed = True

        if not fixed:
            break

        try:
            parsed = json.loads(repaired_str)
            if repairs > 0:
                print(f"[JSON REPAIR] removed {repairs} stray quotes")
                job_token_tracker.add_repairs(repairs)
            return parsed, repairs
        except json.JSONDecodeError as err:
            last_err = err

    raise last_err


def get_effective_reasoning_effort(override_effort: Optional[str] = None) -> Optional[str]:
    if override_effort:
        return override_effort
    if config.REASONING_EFFORT:
        return config.REASONING_EFFORT
    if config.GROQ_MODEL.startswith("openai/gpt-oss"):
        return "low"
    return None


class RollingTokenBucket:
    """Keep a rolling 60-second record of tokens used (prompt + completion) across calls in the process."""
    def __init__(self, window_seconds: float = 60.0):
        self.window_seconds = window_seconds
        self.records: list[tuple[float, int]] = []

    def _prune(self, now: float):
        cutoff = now - self.window_seconds
        self.records = [r for r in self.records if r[0] > cutoff]

    def get_used_tokens(self, now: float) -> int:
        self._prune(now)
        return sum(tokens for _, tokens in self.records)

    async def throttle(self, estimated_tokens: int, tpm_limit: int):
        while True:
            now = time.time()
            self._prune(now)
            used = sum(tokens for _, tokens in self.records)
            if (used + estimated_tokens <= tpm_limit) or not self.records:
                break

            oldest_time, _ = self.records[0]
            sleep_needed = (oldest_time + self.window_seconds) - now + 0.1
            sleep_duration = min(60.0, max(0.1, sleep_needed))
            print(
                f"[TPM THROTTLE] Used {used} + requested {estimated_tokens} > limit {tpm_limit}. "
                f"Sleeping {sleep_duration:.1f}s until tokens in rolling 60s window expire...",
                flush=True,
            )
            job_token_tracker.add_sleep(sleep_duration)
            await asyncio.sleep(sleep_duration)

    def record_usage(self, token_count: int):
        now = time.time()
        self.records.append((now, token_count))
        self._prune(now)


tpm_throttle = RollingTokenBucket(window_seconds=60.0)


KNOWN_MODEL_OTPM_LIMITS: dict[str, int] = {}


def get_safe_max_tokens(prompt: str, requested_max: int, model: Optional[str] = None) -> tuple[int, str]:
    """Dynamically scale max_tokens to stay safely under Groq's TPM and OTPM limits."""
    tpm_limit = getattr(config, "GROQ_TPM_LIMIT", 8000)
    target_model = model or config.GROQ_MODEL
    estimated_prompt_tokens = len(prompt) // 4
    tpm_available = tpm_limit - estimated_prompt_tokens - 300
    tpm_clamp = max(512, tpm_available)

    effective_otpm = getattr(config, "GROQ_OTPM_LIMIT", None) or KNOWN_MODEL_OTPM_LIMITS.get(target_model)
    otpm_clamp = None
    if effective_otpm is not None:
        otpm_clamp = max(256, int(effective_otpm * 0.9))

    safe_tokens = requested_max
    reasons = []

    if safe_tokens > tpm_clamp:
        safe_tokens = tpm_clamp
        reasons.append(f"TPM limit {tpm_limit} (avail: {tpm_available})")

    if otpm_clamp is not None and safe_tokens > otpm_clamp:
        safe_tokens = otpm_clamp
        reasons.append(f"OTPM limit {effective_otpm} (-10% cap = {otpm_clamp})")

    if reasons:
        clamp_reason = "clamped by " + ", ".join(reasons)
    else:
        clamp_reason = "requested budget within limits"

    return safe_tokens, clamp_reason


async def call_llm(
    prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: int = 4096,
    json_mode: bool = True,
    call_budget: Optional[dict] = None,
    call_info: Optional[dict] = None,
    override_reasoning_effort: Optional[str] = None,
    stage_name: str = "Stage",
) -> str:
    """
    Send a prompt to Groq and return the raw response text.
    Logs FULL raw response, finish_reason, model name, and token usage to the server console.
    Retries once with higher token limit if finish_reason == 'length' and budget allows.
    """
    if call_budget is None:
        call_budget = {"calls": 0, "max_calls": 3}

    if call_budget["calls"] >= call_budget["max_calls"]:
        raise ValueError(f"Stage exceeded maximum allowed LLM calls ({call_budget['max_calls']})")

    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    safe_max_tokens, clamp_reason = get_safe_max_tokens(prompt, max_tokens, config.GROQ_MODEL)

    # Check if max_tokens is capped below what the stage needs
    effective_otpm = getattr(config, "GROQ_OTPM_LIMIT", None) or KNOWN_MODEL_OTPM_LIMITS.get(config.GROQ_MODEL)
    if effective_otpm is not None:
        otpm_cap = int(effective_otpm * 0.9)
        if max_tokens > otpm_cap and safe_max_tokens < 1500 and max_tokens >= 2048:
            raise ValueError(
                f"Model {config.GROQ_MODEL} allows ~{effective_otpm} output tokens/min on this tier; stage {stage_name} needs more. Upgrade the tier or choose another model."
            )

    max_rate_retries = 2  # retry at most 2 times for TPM/RPM
    backoff_factor = 2
    initial_delay = 3

    current_json_mode = json_mode

    for attempt in range(max_rate_retries + 1):
        try:
            extra_params = {}
            if current_json_mode:
                extra_params["response_format"] = {"type": "json_object"}

            effective_reasoning_effort = get_effective_reasoning_effort(override_reasoning_effort)
            if effective_reasoning_effort and config.GROQ_MODEL.startswith("openai/gpt-oss"):
                extra_params["reasoning_effort"] = effective_reasoning_effort

            if call_budget["calls"] >= call_budget["max_calls"]:
                raise ValueError(f"Stage exceeded maximum allowed LLM calls ({call_budget['max_calls']})")

            # Rolling 60s TPM window throttle before making the call
            estimated_prompt_tokens = len(prompt) // 4
            estimated_request_tokens = estimated_prompt_tokens + safe_max_tokens
            tpm_limit = getattr(config, "GROQ_TPM_LIMIT", 8000)
            await tpm_throttle.throttle(estimated_request_tokens, tpm_limit)

            call_budget["calls"] += 1
            response = client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=messages,
                temperature=0.2,
                max_tokens=safe_max_tokens,
                **extra_params,
            )

            choice = response.choices[0] if (response and response.choices) else None
            content_raw = choice.message.content if (choice and choice.message) else ""
            content = (content_raw or "").strip()
            finish_reason = getattr(choice, "finish_reason", "unknown") if choice else "unknown"
            model_name = getattr(response, "model", config.GROQ_MODEL)
            usage = getattr(response, "usage", None)

            completion_tokens = getattr(usage, "completion_tokens", 0) if usage else 0
            prompt_tokens = getattr(usage, "prompt_tokens", 0) if usage else 0
            total_tokens = getattr(usage, "total_tokens", estimated_request_tokens) if usage else estimated_request_tokens

            reasoning_tokens = 0
            if usage and hasattr(usage, "completion_tokens_details") and usage.completion_tokens_details:
                reasoning_tokens = getattr(usage.completion_tokens_details, "reasoning_tokens", 0) or 0

            # Record token usage in rolling bucket and job tracker
            tpm_throttle.record_usage(total_tokens)
            job_token_tracker.add_usage(usage)

            if call_info is not None:
                call_info["finish_reason"] = finish_reason
                call_info["model"] = model_name
                call_info["safe_max_tokens"] = safe_max_tokens
                call_info["completion_tokens"] = completion_tokens
                call_info["prompt_tokens"] = prompt_tokens
                call_info["reasoning_tokens"] = reasoning_tokens

            # Track Stage 3 finish reasons
            if "part a" in stage_name.lower() or "call a" in stage_name.lower():
                job_token_tracker.record_stage_3_finish_reason("Call A", finish_reason)
            elif "part b" in stage_name.lower() or "call b" in stage_name.lower():
                job_token_tracker.record_stage_3_finish_reason("Call B", finish_reason)

            # Reasoning share calculations
            reasoning_share_pct = (reasoning_tokens / completion_tokens * 100) if completion_tokens > 0 else 0.0
            output_tokens = max(0, completion_tokens - reasoning_tokens)
            reasoning_log_str = effective_reasoning_effort or "not sent"

            # Whitespace share calculation
            raw_len = len(content_raw) if content_raw else 0
            if raw_len > 0:
                ws_count = sum(1 for c in content_raw if c in (' ', '\n', '\t', '\r'))
                ws_pct = (ws_count / raw_len) * 100
            else:
                ws_count = 0
                ws_pct = 0.0

            # Log FULL raw LLM response and metadata to server console without slicing
            try:
                used_tokens_window = tpm_throttle.get_used_tokens(time.time())
                otpm_limit_disp = getattr(config, "GROQ_OTPM_LIMIT", None) or KNOWN_MODEL_OTPM_LIMITS.get(config.GROQ_MODEL, "unset")
                known_limits_str = f"TPM={tpm_limit}, OTPM={otpm_limit_disp}"

                json_mode_log = "json_object" if current_json_mode else "none"

                print("\n" + "=" * 60)
                print(
                    f"[GROQ LLM INVOCATION] Model: {model_name} | "
                    f"Max Tokens Configured: {safe_max_tokens} (clamp reason: {clamp_reason}) | "
                    f"TPM Used in 60s Window: {used_tokens_window}/{tpm_limit} | "
                    f"Known Model Limits: {known_limits_str} | "
                    f"reasoning_effort={reasoning_log_str} | "
                    f"response_format={json_mode_log}"
                )
                print(
                    f"[GROQ LLM METADATA] Finish Reason: {finish_reason} | Usage: {usage} | "
                    f"Reasoning Tokens: {reasoning_tokens}/{completion_tokens} ({reasoning_share_pct:.1f}% reasoning share, {output_tokens} output tokens) | "
                    f"Whitespace Share: {ws_pct:.1f}% ({ws_count}/{raw_len} chars)"
                )
                if reasoning_share_pct > 50.0:
                    print(
                        f"[GROQ LLM WARNING] High reasoning share detected: {reasoning_share_pct:.1f}% "
                        f"({reasoning_tokens}/{completion_tokens} completion tokens used for reasoning). "
                        f"Only {output_tokens} output tokens remaining."
                    )
                print("[GROQ LLM RAW RESPONSE - FULL UN-SLICED]:")
                import sys
                if hasattr(sys.stdout, "buffer"):
                    sys.stdout.buffer.write((content + "\n").encode("utf-8", errors="replace"))
                    sys.stdout.buffer.flush()
                else:
                    print(content.encode("ascii", errors="backslashreplace").decode("ascii"))
                print("=" * 60 + "\n")
            except Exception as log_err:
                print(f"[GROQ LLM LOG ERROR]: {log_err}")

            # Strip reasoning/think blocks if output is from a reasoning model
            if "<think>" in content:
                parts = content.split("</think>")
                if len(parts) > 1:
                    content = parts[-1].strip()

            # None-safe / Empty content check
            if not content or finish_reason == "length":
                # If reasoning consumed >= 50% of output tokens
                if reasoning_tokens > 0 and completion_tokens > 0 and (reasoning_tokens >= 0.5 * completion_tokens):
                    if (
                        config.GROQ_MODEL.startswith("openai/gpt-oss")
                        and not effective_reasoning_effort
                        and call_budget["calls"] < call_budget["max_calls"]
                    ):
                        print(
                            f"[GROQ LLM] Reasoning consumed {reasoning_tokens} of {completion_tokens} tokens (>=50%). "
                            f"Retrying once with reasoning_effort='low'..."
                        )
                        return await call_llm(
                            prompt=prompt,
                            system_prompt=system_prompt,
                            max_tokens=max_tokens,
                            json_mode=current_json_mode,
                            call_budget=call_budget,
                            call_info=call_info,
                            override_reasoning_effort="low",
                            stage_name=stage_name,
                        )
                    else:
                        raise ValueError(
                            f"Model returned empty content (finish_reason={finish_reason}, "
                            f"reasoning_tokens={reasoning_tokens} of {completion_tokens}). "
                            f"Reasoning consumed the output budget."
                        )

                if not content:
                    raise ValueError(
                        f"Model returned empty content (finish_reason={finish_reason}, "
                        f"completion_tokens={completion_tokens}, max_tokens={safe_max_tokens})."
                    )

                # If finish_reason == "length" and reasoning < 50%, check if max_tokens can be increased
                if finish_reason == "length":
                    new_safe_max_tokens, _ = get_safe_max_tokens(prompt, 8192, config.GROQ_MODEL)
                    if new_safe_max_tokens <= safe_max_tokens:
                        raise ValueError(
                            f"Output exceeded the per-request token budget (clamped to {safe_max_tokens}). "
                            f"Split the stage or use a model with a higher TPM."
                        )
                    if call_budget["calls"] < call_budget["max_calls"]:
                        print(f"[GROQ LLM] Response hit token limit ('length'). Retrying with max_tokens={new_safe_max_tokens}...")
                        return await call_llm(
                            prompt=prompt,
                            system_prompt=system_prompt,
                            max_tokens=8192,
                            json_mode=current_json_mode,
                            call_budget=call_budget,
                            call_info=call_info,
                            override_reasoning_effort=override_reasoning_effort,
                            stage_name=stage_name,
                        )
                    else:
                        raise ValueError(
                            f"Output exceeded token budget (clamped to {safe_max_tokens}) and stage call budget ({call_budget['max_calls']}) is exhausted."
                        )

            return content.strip()

        except Exception as e:
            # 1. Handle Groq JSON validate failed error
            is_json_validate_error = False
            failed_gen_text = ""
            if isinstance(e, groq.BadRequestError) or (hasattr(e, "status_code") and e.status_code == 400):
                body = getattr(e, "body", {})
                if isinstance(body, dict):
                    err_dict = body.get("error", {})
                    if err_dict.get("code") == "json_validate_failed" or "json" in str(err_dict.get("message", "")).lower():
                        is_json_validate_error = True
                        failed_gen_val = err_dict.get("failed_generation", "")
                        failed_gen_text = str(failed_gen_val) if failed_gen_val is not None else ""

            if is_json_validate_error:
                # None-safe check on failed_generation text with deterministic repair
                if failed_gen_text and failed_gen_text.strip():
                    try:
                        parsed, rep_cnt = repair_and_parse_json(failed_gen_text)
                        if parsed:
                            return json.dumps(parsed)
                    except Exception:
                        pass

                if attempt >= max_rate_retries:
                    raise ValueError(f"Groq API call failed (400 json_validate_failed) after {max_rate_retries} retries: {str(e)}")
                print(f"[GROQ LLM] json_validate_failed detected on attempt {attempt+1}. Retrying without strict response_format...")
                current_json_mode = False
                await asyncio.sleep(1)
                continue

            # 2. Real HTTP 413 Payload Too Large
            is_413 = False
            if (isinstance(e, groq.APIStatusError) and e.status_code == 413) or (hasattr(e, "status_code") and e.status_code == 413):
                is_413 = True

            if is_413:
                raise ValueError(f"Groq API payload too large (413): {str(e)}")

            # 3. Rate Limit HTTP 429
            is_rate_limit = False
            if isinstance(e, groq.RateLimitError) or (hasattr(e, "status_code") and e.status_code == 429):
                is_rate_limit = True
            elif "429" in str(e) or "rate limit" in str(e).lower() or "rate_limit" in str(e).lower():
                is_rate_limit = True

            if is_rate_limit:
                call_budget["calls"] = max(0, call_budget["calls"] - 1)

                err_msg = str(e)
                if hasattr(e, "body") and isinstance(e.body, dict):
                    err_dict = e.body.get("error", {})
                    if isinstance(err_dict, dict) and "message" in err_dict:
                        err_msg = err_dict["message"]

                err_msg_lower = err_msg.lower()

                # 3a. Tokens per day (TPD) -> daily limit error (NO RETRY)
                if "tokens per day" in err_msg_lower or "tpd" in err_msg_lower or "per day" in err_msg_lower:
                    import re
                    try_match = re.search(r"try again in [^.]+", err_msg, re.IGNORECASE)
                    try_text = f". {try_match.group(0).rstrip('.')}" if try_match else ""
                    raise ValueError(
                        f"Groq daily token limit reached for model {config.GROQ_MODEL}{try_text}. Wait for the reset or set GROQ_MODEL to another model."
                    )

                # 3b. Output tokens per minute (OTPM) -> output-limit error (NO RETRY)
                if "otpm" in err_msg_lower or "output tokens per minute" in err_msg_lower:
                    import re
                    limit_match = re.search(r"limit\s+(\d+)", err_msg, re.IGNORECASE)
                    if limit_match:
                        n_val = int(limit_match.group(1))
                        KNOWN_MODEL_OTPM_LIMITS[config.GROQ_MODEL] = n_val
                        limit_display = str(n_val)
                    else:
                        limit_display = str(getattr(config, "GROQ_OTPM_LIMIT", "configured"))
                    raise ValueError(
                        f"Model {config.GROQ_MODEL} allows ~{limit_display} output tokens/min on this tier; stage {stage_name} needs more. Upgrade the tier or choose another model."
                    )

                # 3c. Tokens per minute (TPM) / Requests per minute (RPM) -> wait and retry (max 2 retries)
                wait_time = None
                if hasattr(e, "response") and hasattr(e.response, "headers"):
                    retry_after_hdr = e.response.headers.get("retry-after")
                    if retry_after_hdr:
                        try:
                            wait_time = float(retry_after_hdr)
                        except (ValueError, TypeError):
                            pass

                if wait_time is None:
                    import re
                    sec_match = re.search(r"try again in ([\d\.]+)s", err_msg, re.IGNORECASE)
                    if sec_match:
                        try:
                            wait_time = float(sec_match.group(1))
                        except (ValueError, TypeError):
                            pass
                    else:
                        min_sec_match = re.search(r"try again in (?:(\d+)m)?\s*([\d\.]+)s", err_msg, re.IGNORECASE)
                        if min_sec_match:
                            try:
                                mins = float(min_sec_match.group(1) or 0)
                                secs = float(min_sec_match.group(2))
                                wait_time = mins * 60 + secs
                            except (ValueError, TypeError):
                                pass

                if wait_time is None:
                    wait_time = initial_delay * (backoff_factor ** attempt)

                sleep_seconds = min(30.0, max(1.0, wait_time))

                if attempt >= max_rate_retries:
                    raise ValueError(f"Groq API rate limit exceeded after {max_rate_retries} retries: {err_msg}")

                logger.warning(
                    f"Groq rate limit hit. Retrying in {sleep_seconds:.1f}s... (Attempt {attempt+1}/{max_rate_retries})"
                )
                job_token_tracker.add_sleep(sleep_seconds)
                await asyncio.sleep(sleep_seconds)
            else:
                raise ValueError(f"Groq API call failed: {str(e)}")

    raise ValueError(f"Groq API call failed: exceeded maximum retry attempts ({max_rate_retries}).")


# Backward-compatible alias
call_gemini = call_llm


async def call_llm_json_with_retry(
    prompt: str,
    system_prompt: Optional[str] = None,
    max_tokens: int = 4096,
    stage_name: str = "Stage",
    call_budget: Optional[dict] = None,
) -> dict:
    """
    Calls Groq with JSON mode, extracts balanced JSON block, cleans formatting glitches,
    and on parse failure retries feeding back the parse error, strictly capped at 3 total LLM calls per stage.
    """
    current_prompt = prompt
    last_parse_error = None
    last_raw_response = ""
    last_finish_reason = "unknown"
    last_completion_tokens = 0
    safe_max, _ = get_safe_max_tokens(prompt, max_tokens, config.GROQ_MODEL)
    last_safe_max_tokens = safe_max

    if call_budget is None:
        call_budget = {"calls": 0, "max_calls": 3}

    while call_budget["calls"] < call_budget["max_calls"]:
        call_info = {}
        raw_response = await call_llm(
            prompt=current_prompt,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
            json_mode=True,
            call_budget=call_budget,
            call_info=call_info,
            stage_name=stage_name,
        )
        last_raw_response = (raw_response or "")
        last_finish_reason = call_info.get("finish_reason", "unknown")
        last_completion_tokens = call_info.get("completion_tokens", 0)
        last_safe_max_tokens = call_info.get("safe_max_tokens", last_safe_max_tokens)

        try:
            parsed, rep_cnt = repair_and_parse_json(last_raw_response)
            return parsed
        except json.JSONDecodeError as e:
            last_parse_error = str(e)
            print(f"[{stage_name}] JSON decode failed (call {call_budget['calls']}/{call_budget['max_calls']}): {last_parse_error}")

            if call_budget["calls"] < call_budget["max_calls"]:
                # Feedback parse error to the model
                current_prompt = (
                    f"{prompt}\n\n"
                    f"CRITICAL ERROR IN PREVIOUS ATTEMPT:\n"
                    f"Your previous output could not be parsed as valid JSON.\n"
                    f"JSON Parse Error: {last_parse_error}\n"
                    f"Extracted String: {last_raw_response[:300]}...\n\n"
                    f"Please output ONLY valid, well-formed, strict JSON conforming to the schema."
                )
                await asyncio.sleep(1)

    raw_str = last_raw_response or ""
    tail_chars = raw_str[-300:] if len(raw_str) > 300 else raw_str
    raise ValueError(
        f"{stage_name} failed - JSON parse error: {last_parse_error} "
        f"[finish_reason={last_finish_reason}, completion_tokens={last_completion_tokens}, max_tokens={last_safe_max_tokens}]. "
        f"Last 300 chars of raw response:\n{tail_chars}"
    )


# Backward-compatible alias
call_gemini_json_with_retry = call_llm_json_with_retry
