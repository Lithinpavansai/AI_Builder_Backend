import json
from pydantic import ValidationError as PydanticValidationError
from app.validators.models import (
    AppSchema, IntentOutput, SystemDesignOutput,
    ValidationReport, ValidationError,
)


# ---------------------------------------------------------------------------
# HELPER: parse pydantic errors
# ---------------------------------------------------------------------------

def parse_pydantic_errors(e: PydanticValidationError) -> list[ValidationError]:
    errors = []
    for err in e.errors():
        field = " -> ".join(str(loc) for loc in err["loc"])
        errors.append(ValidationError(
            field=field,
            error_type=err["type"],
            message=err["msg"],
            received_value=str(err.get("input", ""))[:100],
        ))
    return errors


# ---------------------------------------------------------------------------
# VALIDATOR 1: validate raw JSON string
# ---------------------------------------------------------------------------

def validate_json(raw: str, stage: str):
    from app.utils.llm import extract_balanced_json
    preview = raw.strip()[:200]
    extracted = extract_balanced_json(raw)

    try:
        parsed = json.loads(extracted)
        return ValidationReport(
            is_valid=True,
            stage=stage,
            errors=[],
            warnings=[],
            raw_input_preview=preview,
        ), parsed
    except json.JSONDecodeError as e:
        return ValidationReport(
            is_valid=False,
            stage=stage,
            errors=[ValidationError(
                field="root",
                error_type="invalid_json",
                message=f"JSON parsing failed: {str(e)}",
                received_value=preview,
            )],
            raw_input_preview=preview,
        ), None


# ---------------------------------------------------------------------------
# VALIDATOR 2: validate IntentOutput
# ---------------------------------------------------------------------------

def validate_intent(data: dict, stage: str = "intent_extraction"):
    try:
        intent = IntentOutput(**data)
        warnings = []
        if len(intent.core_entities) == 0:
            warnings.append("No core entities found — pipeline may produce incomplete schema")
        if len(intent.user_roles) == 0:
            warnings.append("No user roles defined — defaulting to single role app")
        return ValidationReport(is_valid=True, stage=stage, warnings=warnings), intent
    except PydanticValidationError as e:
        return ValidationReport(
            is_valid=False,
            stage=stage,
            errors=parse_pydantic_errors(e),
        ), None


# ---------------------------------------------------------------------------
# VALIDATOR 3: validate SystemDesignOutput
# ---------------------------------------------------------------------------

def validate_system_design(data: dict, stage: str = "system_design"):
    try:
        design = SystemDesignOutput(**data)
        warnings = []
        if len(design.pages) < 2:
            warnings.append("Fewer than 2 pages defined — app may be incomplete")
        if "auth" not in design.api_groups and design.db_tables:
            warnings.append("No auth API group defined despite having DB tables")
        return ValidationReport(is_valid=True, stage=stage, warnings=warnings), design
    except PydanticValidationError as e:
        return ValidationReport(
            is_valid=False,
            stage=stage,
            errors=parse_pydantic_errors(e),
        ), None


# ---------------------------------------------------------------------------
# VALIDATOR 4: validate AppSchema
# ---------------------------------------------------------------------------

def validate_app_schema(data: dict, stage: str = "schema_generation"):
    try:
        schema = AppSchema(**data)
        warnings = []
        if not schema.auth.roles:
            warnings.append("No auth roles defined in final schema")
        if not schema.database.tables:
            warnings.append("No database tables defined in final schema")
        return ValidationReport(is_valid=True, stage=stage, warnings=warnings), schema
    except PydanticValidationError as e:
        return ValidationReport(
            is_valid=False,
            stage=stage,
            errors=parse_pydantic_errors(e),
        ), None
