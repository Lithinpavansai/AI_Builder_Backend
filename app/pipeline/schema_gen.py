# Stage 3 - Schema Generation

from datetime import datetime

from pydantic import ValidationError

from app.utils.config import config
from app.utils.llm import call_llm_json_with_retry
from app.validators.models import (
    AppSchema,
    APISchema,
    AuthSchema,
    BusinessLogicSchema,
    BusinessRule,
    DatabaseSchema,
    IntentOutput,
    PipelineMetadata,
    SystemDesignOutput,
    UISchema,
)


async def generate_schemas(
    intent: IntentOutput, design: SystemDesignOutput
) -> AppSchema:

    # Shared context injected into the prompt
    context = f"""
App Name: {design.app_name}
Entities: {[e.name for e in design.entities]}
Roles: {design.roles}
Pages: {design.pages}
API Groups: {design.api_groups}
DB Tables: {design.db_tables}
Auth Flow: {design.auth_flow}
Business Rules: {design.business_rules}
"""

    system_prompt = (
        "You are a software architect who designs database structures, REST APIs, UI pages, user roles, and business rules.\n"
        "Given an app name, user roles, features, pages, database tables, and business rules, generate a complete and consistent application schema JSON.\n"
        "\n"
        "Return ONLY a valid JSON object with the following exact structure:\n"
        "{\n"
        '  "ui": {\n'
        '    "pages": [\n'
        '      {\n'
        '        "name": "PageName (PascalCase string, e.g., ProjectDashboardPage)",\n'
        '        "route": "/route (string, e.g., /projects)",\n'
        '        "components": [\n'
        '          {\n'
        '            "type": "table|form|chart|card|modal|sidebar|navbar (must be one of these)",\n'
        '            "name": "ComponentName (PascalCase string, e.g., ProjectList)",\n'
        '            "fields": ["list of fields, as strings"],\n'
        '            "actions": ["list of actions, as strings"],\n'
        '            "props": {} (an object with key/value string pairs)\n'
        '          }\n'
        '        ],\n'
        '        "access": ["list of role names allowed to access, e.g., Admin"],\n'
        '        "layout": "default|sidebar|fullscreen"\n'
        '      }\n'
        '    ],\n'
        '    "global_components": []\n'
        '  },\n'
        '  "api": {\n'
        '    "endpoints": [\n'
        '      {\n'
        '        "path": "/path (string, e.g., /api/v1/projects)",\n'
        '        "method": "GET|POST|PUT|DELETE|PATCH",\n'
        '        "description": "what this endpoint does",\n'
        '        "auth_required": true|false,\n'
        '        "roles": ["list of user roles allowed to access"],\n'
        '        "request_body": {"field_name": "type"} (or null/empty),\n'
        '        "response_fields": ["list of field names as strings"],\n'
        '        "validation_rules": {"field_name": "rule description"} (or null/empty)\n'
        '      }\n'
        '    ],\n'
        '    "base_url": "/api/v1",\n'
        '    "auth_endpoint": "/api/v1/auth/login"\n'
        '  },\n'
        '  "database": {\n'
        '    "tables": [\n'
        '      {\n'
        '        "name": "lowercase_table_name",\n'
        '        "columns": [\n'
        '          {\n'
        '            "name": "column_name",\n'
        '            "type": "integer|string|text|boolean|float|datetime|json (must be one of these)",\n'
        '            "primary_key": true|false,\n'
        '            "nullable": true|false,\n'
        '            "unique": true|false,\n'
        '            "default": "default_value as a string or null"\n'
        '          }\n'
        '        ],\n'
        '        "relations": [\n'
        '          {\n'
        '            "type": "one_to_many|many_to_one|many_to_many|one_to_one (must be one of these)",\n'
        '            "target_table": "table_name",\n'
        '            "foreign_key": "column_name"\n'
        '          }\n'
        '        ]\n'
        '      }\n'
        '    ],\n'
        '    "database_type": "postgresql"\n'
        '  },\n'
        '  "auth": {\n'
        '    "roles": ["list of user roles"],\n'
        '    "permissions": {\n'
        '      "role_name": ["list of permission strings"]\n'
        '    },\n'
        '    "auth_method": "jwt",\n'
        '    "token_expiry": "24h",\n'
        '    "refresh_token": true\n'
        '  },\n'
        '  "business_logic": {\n'
        '    "rules": [\n'
        '      {\n'
        '        "name": "rule_name_snake_case",\n'
        '        "description": "description",\n'
        '        "condition": "logical condition",\n'
        '        "affected_routes": ["list of affected API endpoint paths"],\n'
        '        "action": "action description"\n'
        '      }\n'
        '    ]\n'
        '  }\n'
        '}\n'
        "\n"
        "Design Constraints:\n"
        "1. Every page defined in the list of pages must be generated in the ui.pages section. Every generated page must contain at least one UI component.\n"
        "2. Every page the user can navigate to must be backed by a working API endpoint. The page's route (excluding home, login, register, and empty routes) must match or be a substring of the API endpoint path.\n"
        "3. Every API endpoint that requires authentication must specify which user role(s) can access it from the Auth roles list.\n"
        "4. Database table names referenced by API endpoints must exactly match table names defined in the DB section.\n"
        "5. Foreign key relationships in the DB section must only point to tables that exist in the same schema.\n"
        "6. Any route referenced by a business rule must be a route that actually exists in the API section.\n"
        "7. All roles used in UI page access settings and API endpoints must be defined in the main Auth configuration roles list.\n"
        "8. Every database table must contain an 'id' primary key column (integer type) and a 'created_at' column (datetime type).\n"
        "9. Generate essential REST endpoints (GET list, POST create, GET/PUT/DELETE by ID) for main resources. Keep descriptions and validation rules concise.\n"
        "10. Return ONLY valid, well-formed JSON. Do NOT output comments, asterisks, shorthand placeholders, or markdown fences.\n"
        "11. Every element in the endpoints and pages arrays must be a complete JSON object, not a string or comment."
    )

    # Call A: Generate UI and API layers
    prompt_a = (
        f"{system_prompt}\n\n"
        f"App Design Context:\n{context}\n\n"
        f"INSTRUCTION FOR THIS CALL:\n"
        f"Return ONLY a JSON object with top-level keys: ui, api.\n"
        f"Ensure every endpoint in api.endpoints is a valid JSON object. Do not abbreviate or use comments. Output minified JSON."
    )

    from app.utils.llm import get_safe_max_tokens, normalize_part
    tpm_limit = getattr(config, "GROQ_TPM_LIMIT", 12000)
    max_budget_allowed = tpm_limit - 300

    prompt_a_est = len(prompt_a) // 4
    safe_max_a, clamp_reason_a = get_safe_max_tokens(prompt_a, 4096, config.GROQ_MODEL)
    sum_a = prompt_a_est + safe_max_a
    print(
        f"[STAGE 3 BUDGET - CALL A] Estimated Prompt Tokens: {prompt_a_est} | "
        f"Max Tokens Clamped: {safe_max_a} | Sum: {sum_a} (Max Allowed: {max_budget_allowed}) | "
        f"Status: {'Fits' if sum_a <= max_budget_allowed and safe_max_a >= 2000 else 'Exceeds budget'}",
        flush=True
    )
    if safe_max_a < 2000:
        deficit = 2000 - safe_max_a
        raise ValueError(
            f"Stage 3 Call A clamped to {safe_max_a} tokens (<2000 min required by {deficit} tokens). "
            f"Prompt {prompt_a_est} tokens exceeds available TPM budget."
        )

    call_a_raw = await call_llm_json_with_retry(
        prompt=prompt_a,
        max_tokens=4096,
        stage_name="Schema Generation (Part A: UI & API)",
    )
    call_a_data = normalize_part(call_a_raw, "A")

    # Extract alignment context from Call A for Call B
    endpoints = call_a_data.get("api", {}).get("endpoints", [])
    ep_paths = [ep.get("path") for ep in endpoints if isinstance(ep, dict) and "path" in ep]
    ep_roles = [r for ep in endpoints if isinstance(ep, dict) for r in ep.get("roles", [])]
    page_roles = [r for page in call_a_data.get("ui", {}).get("pages", []) if isinstance(page, dict) for r in page.get("access", [])]
    roles_used = sorted(list(set(design.roles + ep_roles + page_roles)))

    alignment_context = (
        f"Context and Alignment from Part A (UI & API):\n"
        f"- Generated API Endpoint Paths: {ep_paths}\n"
        f"- Roles used across UI and API: {roles_used}\n"
        f"- Target Database Tables: {design.db_tables}\n"
    )

    # Call B: Generate Database, Auth, Business Logic layers
    prompt_b = (
        f"{system_prompt}\n\n"
        f"App Design Context:\n{context}\n\n"
        f"{alignment_context}\n"
        f"INSTRUCTION FOR THIS CALL:\n"
        f"Return ONLY the keys: database, auth, business_logic, and any other remaining top-level keys of the schema.\n"
        f"Ensure table names and role names match the alignment context above.\n"
        f"Output minified JSON with no unnecessary whitespace or newlines. No newlines or indentation.\n"
        f'Return one JSON OBJECT, not an array, with exactly these top-level keys: {{"database":{{...}},"auth":{{...}},"business_logic":{{...}}}}. '
        f'Do NOT include ui or api. Each key must be followed directly by its value (write "auth":{{...}}, never "auth", ":", {{...}}). Minified, no newlines.'
    )

    prompt_b_est = len(prompt_b) // 4
    safe_max_b, clamp_reason_b = get_safe_max_tokens(prompt_b, 3500, config.GROQ_MODEL)
    sum_b = prompt_b_est + safe_max_b
    print(
        f"[STAGE 3 BUDGET - CALL B] Estimated Prompt Tokens: {prompt_b_est} | "
        f"Max Tokens Clamped: {safe_max_b} | Sum: {sum_b} (Max Allowed: {max_budget_allowed}) | "
        f"Status: {'Fits' if sum_b <= max_budget_allowed and safe_max_b >= 1800 else 'Exceeds budget'}",
        flush=True
    )
    if safe_max_b < 1800:
        deficit = 1800 - safe_max_b
        raise ValueError(
            f"Stage 3 Call B clamped to {safe_max_b} tokens (<1800 min required by {deficit} tokens). "
            f"Prompt {prompt_b_est} tokens exceeds available TPM budget."
        )

    call_b_raw = await call_llm_json_with_retry(
        prompt=prompt_b,
        max_tokens=3500,
        stage_name="Schema Generation (Part B: DB & Auth)",
    )
    call_b_data = normalize_part(call_b_raw, "B")

    # Merge A and B into one complete dictionary
    parsed_data = {**call_a_data, **call_b_data}

    # Validate individual parts of the JSON with Pydantic
    try:
        ui_schema = UISchema(**parsed_data.get("ui", {}))
        api_schema = APISchema(**parsed_data.get("api", {}))
        db_schema = DatabaseSchema(**parsed_data.get("database", {}))
        auth_schema = AuthSchema(**parsed_data.get("auth", {}))
        bl_schema = BusinessLogicSchema(**parsed_data.get("business_logic", {}))
    except ValidationError as e:
        raise ValueError(f"Schema compilation model validation failed: {str(e)}")

    metadata = PipelineMetadata(
        generated_at=datetime.utcnow().isoformat(),
        pipeline_version="1.0.0",
        assumptions=intent.assumptions,
        warnings=[],
    )

    app_schema = AppSchema(
        app_name=design.app_name,
        description=f"{design.app_name} - Generated by App Compiler",
        ui=ui_schema,
        api=api_schema,
        database=db_schema,
        auth=auth_schema,
        business_logic=bl_schema,
        metadata=metadata,
    )

    return app_schema
