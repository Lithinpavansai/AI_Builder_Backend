from app.validators.models import (
    AppSchema, UISchema, UIPage, UIComponent,
    APISchema, APIEndpoint, HTTPMethod,
    DatabaseSchema, DBTable, DBColumn,
    AuthSchema, BusinessLogicSchema, BusinessRule, PipelineMetadata
)
from app.pipeline.refinement import check_consistency
from app.validators.runtime_validator import validate_runtime

# Scenario:
# - Auth roles include BOTH "Admin" and "Customer" (valid_roles = {"admin", "customer"})
# - An API endpoint/rule is Admin-only: roles=["Admin"]
# - The UI/config lets Customer access it: UIPage.access = ["Customer"]
schema = AppSchema(
    app_name="TestApp",
    description="Test App for Admin vs Customer bug",
    ui=UISchema(
        pages=[
            UIPage(
                name="AnalyticsPage",
                route="/analytics",
                access=["Customer"],  # UI allows Customer!
                layout="default",
                components=[
                    UIComponent(type="chart", name="AnalyticsChart", fields=["metric", "value"], actions=[], props={})
                ]
            )
        ]
    ),
    api=APISchema(
        base_url="/api/v1",
        auth_endpoint="/api/v1/auth/login",
        endpoints=[
            APIEndpoint(
                path="/api/v1/analytics",
                method=HTTPMethod.GET,
                description="Admin-only analytics endpoint",
                auth_required=True,
                roles=["Admin"],  # API allows only Admin!
                request_body=None,
                response_fields=["metric", "value"],
                validation_rules={}
            )
        ]
    ),
    database=DatabaseSchema(
        database_type="postgresql",
        tables=[
            DBTable(
                name="analytics",
                columns=[
                    DBColumn(name="id", type="integer", primary_key=True, nullable=False, unique=True),
                    DBColumn(name="created_at", type="datetime", primary_key=False, nullable=False, unique=False),
                    DBColumn(name="metric", type="string"),
                    DBColumn(name="value", type="float")
                ],
                relations=[]
            )
        ]
    ),
    auth=AuthSchema(
        roles=["Admin", "Customer"],  # BOTH defined in auth.roles!
        permissions={
            "Admin": ["view_analytics", "manage_all"],
            "Customer": ["view_profile"]
        },
        auth_method="jwt",
        token_expiry="24h",
        refresh_token=True
    ),
    business_logic=BusinessLogicSchema(
        rules=[
            BusinessRule(
                name="admin_only_analytics",
                description="Only Admin can access analytics",
                condition="user.role == 'Admin'",
                affected_routes=["/api/v1/analytics"],
                action="allow Admin only"
            )
        ]
    ),
    metadata=PipelineMetadata(
        generated_at="2026-01-01T00:00:00",
        pipeline_version="1.0.0",
        assumptions=[],
        warnings=[]
    )
)

print("=== REFINEMENT CHECKER (check_consistency) ===")
refinement_issues = check_consistency(schema)
print("Issues found:", refinement_issues)

print("\n=== RUNTIME VALIDATOR (validate_runtime) ===")
runtime_result = validate_runtime(schema)
print("Score:", runtime_result["score"])
print("Executable:", runtime_result["executable"])
for c in runtime_result["checks"]:
    print(f"  Check '{c['check']}': passed={c['passed']}, details='{c['details']}', affected={c.get('affected')}")
