# Stage 1 - Intent Extraction

from pydantic import ValidationError

from app.utils.config import config
from app.utils.llm import call_llm_json_with_retry
from app.validators.models import IntentEntity, IntentOutput


async def extract_intent(prompt: str) -> IntentOutput:
    system_prompt = (
        "You are an intent extraction engine for an app generation system.\n"
        "Your job is to analyze a user's app description and extract structured intent.\n"
        "\n"
        "Return ONLY a valid JSON object with this exact structure:\n"
        "{\n"
        '  "app_name": "string - a suitable name for the app",\n'
        '  "app_type": "string - type of app (crm, ecommerce, dashboard, social, productivity, etc)",\n'
        '  "core_entities": [\n'
        "    {\n"
        '      "name": "string - entity name (singular, PascalCase)",\n'
        '      "attributes": ["list of attribute names as strings"],\n'
        '      "relationships": ["list of relationship descriptions as strings"]\n'
        "    }\n"
        "  ],\n"
        '  "user_roles": ["list of user role names"],\n'
        '  "core_features": ["list of core features as strings"],\n'
        '  "auth_required": true/false,\n'
        '  "payment_required": true/false,\n'
        '  "assumptions": ["list of assumptions you made as strings"]\n'
        "}\n"
        "\n"
        "Rules:\n"
        "- Return ONLY the JSON object. No markdown, no explanation, no code fences.\n"
        "- Always include at least one entity\n"
        "- Always include at least one role\n"
        "- If auth is mentioned or implied, set auth_required to true\n"
        "- Document every assumption you make in the assumptions array\n"
        "- Entity names must be PascalCase singular nouns"
    )

    full_prompt = f"{system_prompt}\n\nUser's app description:\n{prompt}"
    max_tokens = 4096

    parsed_data = await call_llm_json_with_retry(
        prompt=full_prompt,
        max_tokens=max_tokens,
        stage_name="Intent Extraction",
    )

    # Validate with Pydantic
    try:
        intent_output = IntentOutput(**parsed_data)
    except ValidationError as e:
        raise ValueError(f"Intent extraction schema mismatch: {str(e)}")

    return intent_output
