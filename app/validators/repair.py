import json
from app.utils.llm import call_llm
from app.validators.models import (
    AppSchema, IntentOutput, SystemDesignOutput,
    DatabaseSchema, APISchema, UISchema, AuthSchema,
    BusinessLogicSchema, ValidationReport, ValidationError,
)
from app.validators.validator import (
    validate_json, validate_intent, validate_system_design, validate_app_schema,
)

MAX_RETRIES = 3


# ---------------------------------------------------------------------------
# REPAIR FUNCTION 1: Repair Intent
# ---------------------------------------------------------------------------

async def repair_intent(
    prompt: str,
    failed_report: ValidationReport,
    attempt: int = 1,
) -> tuple[IntentOutput, ValidationReport]:
    if attempt > MAX_RETRIES:
        raise ValueError(
            f"Intent extraction failed after {MAX_RETRIES} repair attempts. "
            f"Errors: {[e.message for e in failed_report.errors]}"
        )

    error_summary = "\n".join(f"- {e.field}: {e.message}" for e in failed_report.errors)

    repair_prompt = f"""You are an intent extraction engine. Your previous attempt failed validation.

ERRORS FROM PREVIOUS ATTEMPT:
{error_summary}

Fix these specific errors and return a valid JSON object with this exact structure:
{{
  "app_name": "string",
  "app_type": "string",
  "core_entities": [
    {{"name": "PascalCase string", "attributes": ["list of strings"], "relationships": ["list of strings"]}}
  ],
  "user_roles": ["list of role name strings"],
  "core_features": ["list of feature strings"],
  "auth_required": true or false,
  "payment_required": true or false,
  "assumptions": ["list of assumption strings"]
}}

Return ONLY valid JSON. No markdown, no explanation.

Original user prompt: {prompt}"""

    raw = await call_llm(repair_prompt, max_tokens=1536, json_mode=True)
    report, parsed = validate_json(raw, "intent_repair")
    if not report.is_valid or parsed is None:
        return await repair_intent(prompt, report, attempt + 1)

    intent_report, intent = validate_intent(parsed, "intent_repair")
    if not intent_report.is_valid or intent is None:
        return await repair_intent(prompt, intent_report, attempt + 1)

    intent_report.warnings.append(f"Repaired successfully on attempt {attempt}")
    return intent, intent_report


# ---------------------------------------------------------------------------
# REPAIR FUNCTION 2: Repair specific AppSchema layer
# ---------------------------------------------------------------------------

async def repair_schema_layer(
    layer_name: str,
    failed_report: ValidationReport,
    context: str,
    current_schema: dict,
    attempt: int = 1,
) -> dict:
    if attempt > MAX_RETRIES:
        raise ValueError(f"{layer_name} repair failed after {MAX_RETRIES} attempts")

    error_summary = "\n".join(f"- {e.field}: {e.message}" for e in failed_report.errors)
    warning_summary = "\n".join(f"- {w}" for w in failed_report.warnings)

    repair_prompt = f"""You are a schema repair engine.
The '{layer_name}' section of an app schema has validation errors.
Fix ONLY the '{layer_name}' section and return the complete corrected schema.

ERRORS TO FIX:
{error_summary}

WARNINGS TO ADDRESS:
{warning_summary}

RULES:
- Return ONLY the complete corrected JSON schema
- Fix only what is listed in errors/warnings
- Do not change other sections
- No markdown, no explanation

APP CONTEXT:
{context}

CURRENT SCHEMA WITH ERRORS:
{json.dumps(current_schema, indent=2)[:3000]}"""

    raw = await call_llm(repair_prompt, max_tokens=3584, json_mode=True)
    report, parsed = validate_json(raw, f"{layer_name}_repair")

    if not report.is_valid or parsed is None:
        return await repair_schema_layer(
            layer_name, failed_report, context, current_schema, attempt + 1
        )

    return parsed


# ---------------------------------------------------------------------------
# MAIN REPAIR ORCHESTRATOR
# ---------------------------------------------------------------------------

async def run_with_repair(prompt: str) -> tuple[AppSchema, list[ValidationReport]]:
    from app.pipeline.intent import extract_intent
    from app.pipeline.system_design import design_system
    from app.pipeline.schema_gen import generate_schemas
    from app.pipeline.refinement import refine_schema

    reports: list[ValidationReport] = []

    # Stage 1: Intent with repair
    try:
        intent = await extract_intent(prompt)
        intent_report, _ = validate_intent(intent.model_dump(), "intent_extraction")
        reports.append(intent_report)

        if not intent_report.is_valid:
            intent, repaired_report = await repair_intent(prompt, intent_report)
            reports.append(repaired_report)
    except Exception as e:
        raise ValueError(f"Intent stage failed: {str(e)}")

    # Stage 2: System Design with repair
    try:
        design = await design_system(intent)
        design_report, _ = validate_system_design(design.model_dump(), "system_design")
        reports.append(design_report)
    except Exception as e:
        raise ValueError(f"System design stage failed: {str(e)}")

    # Stage 3: Schema Generation with repair
    try:
        schema = await generate_schemas(intent, design)
        schema_report, validated = validate_app_schema(schema.model_dump(), "schema_generation")
        reports.append(schema_report)

        if not schema_report.is_valid:
            context = (
                f"App: {design.app_name}, "
                f"Roles: {design.roles}, "
                f"Tables: {design.db_tables}"
            )
            repaired_data = await repair_schema_layer(
                "full_schema",
                schema_report,
                context,
                schema.model_dump(),
            )
            schema_report2, schema = validate_app_schema(repaired_data, "schema_repair")
            reports.append(schema_report2)
            if schema is None:
                raise ValueError("Schema repair failed after all attempts")
    except Exception as e:
        raise ValueError(f"Schema generation stage failed: {str(e)}")

    # Stage 4: Refinement
    try:
        final_schema = await refine_schema(schema)
    except Exception as e:
        final_schema = schema
        reports.append(ValidationReport(
            is_valid=False,
            stage="refinement",
            warnings=[f"Refinement failed, using unrefined schema: {str(e)}"],
        ))

    return final_schema, reports
