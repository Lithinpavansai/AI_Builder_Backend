from enum import Enum
from typing import Dict, List, Optional, Union

from pydantic import BaseModel, Field, field_validator

import datetime


# ---------------------------------------------------------------------------
# Request Model
# ---------------------------------------------------------------------------

class GenerateRequest(BaseModel):
    prompt: str


# ---------------------------------------------------------------------------
# UI MODELS
# ---------------------------------------------------------------------------

class ComponentType(str, Enum):
    table = "table"
    form = "form"
    chart = "chart"
    card = "card"
    modal = "modal"
    sidebar = "sidebar"
    navbar = "navbar"

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            return cls.card
        val_lower = value.lower().strip()
        if val_lower in ["list", "board", "grid", "card_list", "item_list", "feed", "cards", "kanban", "item", "detail"]:
            return cls.card
        if val_lower in ["datatable", "data_table", "table_view", "grid_table"]:
            return cls.table
        if val_lower in ["filter", "search", "inputs", "login_form", "register_form", "input", "button", "controls"]:
            return cls.form
        if val_lower in ["graph", "plot", "analytics", "metric", "metrics", "kpi", "stats"]:
            return cls.chart
        if val_lower in ["dialog", "popup", "drawer", "alert", "modal_dialog"]:
            return cls.modal
        if val_lower in ["aside", "nav_sidebar", "side_nav", "side_menu", "navigation_drawer"]:
            return cls.sidebar
        if val_lower in ["header", "nav", "menu", "topbar", "top_nav", "navigation_bar"]:
            return cls.navbar
        for member in cls:
            if member.value == val_lower:
                return member
        return cls.card


class UIComponent(BaseModel):
    type: ComponentType
    name: str
    fields: List[str] = []
    actions: List[str] = []
    props: Dict[str, str] = Field(default_factory=dict)

    @field_validator('type', mode='before')
    @classmethod
    def coerce_component_type(cls, v):
        if isinstance(v, ComponentType):
            return v
        if isinstance(v, str):
            try:
                return ComponentType(v)
            except ValueError:
                return ComponentType.card
        return ComponentType.card

    @field_validator('props', mode='before')
    @classmethod
    def coerce_props_none_to_dict(cls, v):
        if v is None:
            return {}
        return v


class UIPage(BaseModel):
    name: str
    route: str
    components: List[UIComponent] = []
    access: List[str] = []
    layout: Optional[str] = "default"

    @field_validator('components', mode='before')
    @classmethod
    def coerce_components_none_to_list(cls, v):
        if v is None:
            return []
        return v


class UISchema(BaseModel):
    pages: List[UIPage] = []
    global_components: List[UIComponent] = []


# ---------------------------------------------------------------------------
# API MODELS
# ---------------------------------------------------------------------------

class HTTPMethod(str, Enum):
    GET = "GET"
    POST = "POST"
    PUT = "PUT"
    DELETE = "DELETE"
    PATCH = "PATCH"

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            return cls.GET
        val_upper = value.upper().strip()
        for member in cls:
            if member.value == val_upper:
                return member
        return cls.GET


class APIEndpoint(BaseModel):
    path: str
    method: HTTPMethod
    description: str
    auth_required: bool = True
    roles: List[str] = []
    request_body: Optional[Dict[str, str]] = None
    response_fields: List[str] = []
    validation_rules: Dict[str, str] = Field(default_factory=dict)

    @field_validator('method', mode='before')
    @classmethod
    def coerce_method(cls, v):
        if isinstance(v, HTTPMethod):
            return v
        if isinstance(v, str):
            try:
                return HTTPMethod(v)
            except ValueError:
                return HTTPMethod.GET
        return HTTPMethod.GET

    @field_validator('validation_rules', 'request_body', mode='before')
    @classmethod
    def coerce_none_to_dict(cls, v):
        if v is None:
            return {}
        return v


class APISchema(BaseModel):
    endpoints: List[APIEndpoint] = []
    base_url: str = "/api/v1"
    auth_endpoint: str = "/api/auth/login"


# ---------------------------------------------------------------------------
# DATABASE MODELS
# ---------------------------------------------------------------------------

class ColumnType(str, Enum):
    integer = "integer"
    string = "string"
    text = "text"
    boolean = "boolean"
    float_ = "float"
    datetime_ = "datetime"
    json = "json"

    @classmethod
    def _missing_(cls, value):
        if not isinstance(value, str):
            return cls.string
        val_lower = value.lower().strip()
        if val_lower in ["varchar", "char", "string", "uuid", "email", "url"]:
            return cls.string
        if val_lower in ["int", "integer", "bigint", "smallint", "serial", "bigserial", "number"]:
            return cls.integer
        if val_lower in ["float", "double", "decimal", "numeric", "real"]:
            return cls.float_
        if val_lower in ["datetime", "timestamp", "date", "time", "timestamptz"]:
            return cls.datetime_
        if val_lower in ["bool", "boolean"]:
            return cls.boolean
        if val_lower in ["jsonb", "json", "object", "array"]:
            return cls.json
        if val_lower in ["text", "longtext", "clob"]:
            return cls.text

        for member in cls:
            if member.value == val_lower:
                return member
        return cls.string


class DBColumn(BaseModel):
    name: str
    type: ColumnType
    primary_key: bool = False
    nullable: bool = True
    unique: bool = False
    default: Optional[Union[str, int, bool, float]] = None

    @field_validator('default', mode='before')
    @classmethod
    def coerce_default_to_string(cls, v):
        if v is None:
            return None
        return str(v)


class DBRelation(BaseModel):
    type: str  # one_to_one, one_to_many, many_to_one, many_to_many
    target_table: str
    foreign_key: str


class DBTable(BaseModel):
    name: str
    columns: List[DBColumn]
    relations: List[DBRelation] = []


class DatabaseSchema(BaseModel):
    tables: List[DBTable]
    database_type: str = "postgresql"


# ---------------------------------------------------------------------------
# AUTH MODELS
# ---------------------------------------------------------------------------

class AuthSchema(BaseModel):
    roles: List[str]
    permissions: Dict[str, List[str]]
    auth_method: str = "jwt"
    token_expiry: str = "24h"
    refresh_token: bool = True


# ---------------------------------------------------------------------------
# BUSINESS LOGIC MODELS
# ---------------------------------------------------------------------------

class BusinessRule(BaseModel):
    name: str
    description: str
    condition: str
    affected_routes: List[str] = []
    action: str


class BusinessLogicSchema(BaseModel):
    rules: List[BusinessRule] = []


# ---------------------------------------------------------------------------
# METADATA MODEL
# ---------------------------------------------------------------------------

class PipelineMetadata(BaseModel):
    generated_at: str
    pipeline_version: str = "1.0.0"
    assumptions: List[str] = []
    warnings: List[str] = []


# ---------------------------------------------------------------------------
# ROOT OUTPUT MODEL
# ---------------------------------------------------------------------------

class AppSchema(BaseModel):
    app_name: str
    description: str
    ui: UISchema
    api: APISchema
    database: DatabaseSchema
    auth: AuthSchema
    business_logic: BusinessLogicSchema
    metadata: PipelineMetadata


# ---------------------------------------------------------------------------
# INTERMEDIATE MODELS (used between pipeline stages)
# ---------------------------------------------------------------------------

class IntentEntity(BaseModel):
    name: str
    attributes: List[str]
    relationships: List[str] = []


class IntentOutput(BaseModel):
    app_name: str
    app_type: str
    core_entities: List[IntentEntity]
    user_roles: List[str]
    core_features: List[str]
    auth_required: bool
    payment_required: bool = False
    assumptions: List[str] = []


class SystemDesignOutput(BaseModel):
    app_name: str
    entities: List[IntentEntity]
    roles: List[str]
    pages: List[str]
    api_groups: List[str]
    db_tables: List[str]
    auth_flow: str
    business_rules: List[str] = []


# ---------------------------------------------------------------------------
# VALIDATION MODELS
# ---------------------------------------------------------------------------

class ValidationError(BaseModel):
    field: str
    error_type: str
    message: str
    received_value: Optional[str] = None


class ValidationReport(BaseModel):
    is_valid: bool
    stage: str
    errors: List[ValidationError] = []
    warnings: List[str] = []
    raw_input_preview: Optional[str] = None
