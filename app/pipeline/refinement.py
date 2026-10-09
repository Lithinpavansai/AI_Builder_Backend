# Stage 4 - Refinement

import json
from app.utils.llm import call_llm_json_with_retry
from app.validators.models import AppSchema
from app.utils.config import config


def check_consistency(schema: AppSchema) -> list[str]:
    issues = []

    # Ground truth sets
    valid_roles = set(r.lower() for r in schema.auth.roles)
    valid_table_names = set(t.name.lower() for t in schema.database.tables)
    valid_api_paths = set(e.path for e in schema.api.endpoints)

    # Check 1: UI page access roles
    for page in schema.ui.pages:
        for role in page.access:
            if role.lower() not in valid_roles:
                issues.append(f"UI page '{page.name}' references undefined role '{role}'")

    # Check 2: API endpoint roles
    for endpoint in schema.api.endpoints:
        for role in endpoint.roles:
            if role.lower() not in valid_roles:
                issues.append(f"API endpoint '{endpoint.path}' references undefined role '{role}'")

    # Check 3: Auth permissions keys
    for role_key in schema.auth.permissions.keys():
        if role_key.lower() not in valid_roles:
            issues.append(f"Auth permissions has undefined role key '{role_key}'")

    # Check 4: DB relation targets (flexible singular/plural matching)
    for table in schema.database.tables:
        for relation in table.relations:
            tgt = relation.target_table.lower()
            if tgt not in valid_table_names and not any(tgt.rstrip("s") == t.rstrip("s") for t in valid_table_names):
                issues.append(f"Table '{table.name}' has relation to undefined table '{relation.target_table}'")

    # Check 5: Business rule affected routes
    for rule in schema.business_logic.rules:
        for route in rule.affected_routes:
            # Normalize: strip path params for comparison
            normalized = route.split("{")[0].rstrip("/")
            found = any(
                ep.path.split(":")[0].rstrip("/") == normalized or
                ep.path == route or
                normalized in ep.path
                for ep in schema.api.endpoints
            )
            if not found:
                issues.append(f"Business rule '{rule.name}' references undefined route '{route}'")

    # Check 6: UI Page Route Completeness (routes must map to API paths)
    for page in schema.ui.pages:
        route = page.route.strip("/")
        has_match = any(
            p.strip("/").startswith(route) or route in p or route.rstrip("s") in p
            for p in valid_api_paths
        )
        if not has_match and route not in ["login", "register", "home", "dashboard", "settings", "admin", "landing", ""]:
            issues.append(f"UI page '{page.name}' with route '{page.route}' has no matching API endpoint path")

    # Check 7: API Endpoint Auth Coverage (auth_required endpoints must have roles)
    for endpoint in schema.api.endpoints:
        if endpoint.auth_required and len(endpoint.roles) == 0:
            issues.append(f"API endpoint '{endpoint.path}' requires authentication but has no roles defined")

    # Check 8: API Endpoint DB Coverage (flexible singular/plural matching)
    api_groups = set()
    for endpoint in schema.api.endpoints:
        parts = endpoint.path.strip("/").split("/")
        if len(parts) >= 3:
            api_groups.add(parts[2].lower())
    for g in api_groups:
        is_known = (
            g in valid_table_names
            or any(g.rstrip("s") == t.rstrip("s") for t in valid_table_names)
            or g in ["auth", "analytics", "search", "reports", "report", "dashboard", "settings"]
        )
        if not is_known:
            issues.append(f"API group '{g}' references undefined database table '{g}'")

    return issues


def auto_fix_consistency(schema: AppSchema) -> list[str]:
    """
    Deterministically fixes common inconsistencies in 0ms before triggering an LLM call:
    - Normalizes roles between UI/API and Auth
    - Normalizes singular/plural table names in relations
    - Fills default roles for auth_required endpoints with empty roles
    - Synchronizes permissions dictionary keys
    """
    fixed = []
    
    # 1. Collect all roles used across layers
    known_roles = {r.strip() for r in schema.auth.roles if r.strip()}
    roles_map = {r.lower(): r for r in known_roles}

    # UI Pages: align casing or add missing roles
    for page in schema.ui.pages:
        new_access = []
        for role in page.access:
            r_lower = role.lower()
            if r_lower in roles_map:
                new_access.append(roles_map[r_lower])
            else:
                roles_map[r_lower] = role
                known_roles.add(role)
                new_access.append(role)
                fixed.append(f"Added role '{role}' from page '{page.name}' to Auth")
        page.access = new_access

    # API Endpoints: align casing or add missing roles
    for endpoint in schema.api.endpoints:
        if endpoint.auth_required and len(endpoint.roles) == 0:
            endpoint.roles = list(known_roles)
            fixed.append(f"Assigned default roles to endpoint '{endpoint.path}'")
        else:
            new_roles = []
            for role in endpoint.roles:
                r_lower = role.lower()
                if r_lower in roles_map:
                    new_roles.append(roles_map[r_lower])
                else:
                    roles_map[r_lower] = role
                    known_roles.add(role)
                    new_roles.append(role)
                    fixed.append(f"Added role '{role}' from endpoint '{endpoint.path}' to Auth")
            endpoint.roles = new_roles

    # Auth schema: update roles list and permissions map
    schema.auth.roles = sorted(list(known_roles))
    for role in schema.auth.roles:
        if role not in schema.auth.permissions:
            schema.auth.permissions[role] = ["read", "write"]
            fixed.append(f"Added default permissions for role '{role}'")

    # DB Relations: align singular/plural target tables
    for table in schema.database.tables:
        for relation in table.relations:
            tgt = relation.target_table.lower()
            for real_t in schema.database.tables:
                if tgt.rstrip("s") == real_t.name.lower().rstrip("s"):
                    relation.target_table = real_t.name
                    break

    return fixed


async def refine_schema(schema: AppSchema) -> AppSchema:
    # Step 1: Automatic deterministic fix in 0ms
    auto_fixed = auto_fix_consistency(schema)
    if auto_fixed:
        schema.metadata.warnings.extend([f"Auto-fixed: {f}" for f in auto_fixed])

    # Step 2: Run consistency checks
    issues = check_consistency(schema)

    # Fast path: no remaining issues
    if not issues:
        schema.metadata.warnings.append("Refinement passed: all layers consistent")
        return schema

    # Step 3: If complex issues remain, call Groq to fix
    schema.metadata.warnings.extend(issues)
    issues_text = "\n".join(f"- {issue}" for issue in issues)
    schema_json = schema.model_dump_json()

    fix_prompt = f"""You are a schema refinement engine for an app generation system.
You have been given a complete app schema that has some inconsistencies.
Your job is to fix ONLY the listed inconsistencies and return the corrected schema.

INCONSISTENCIES FOUND:
{issues_text}

RULES FOR FIXING:
- Fix only what is listed above
- Do not change anything else
- Roles must be consistent across ui, api, and auth sections
- DB relation target_table values must match actual table names exactly
- Business rule affected_routes must match actual API endpoint paths
- Return ONLY the complete corrected JSON schema, no markdown, no explanation

CURRENT SCHEMA:
{schema_json}"""

    max_tokens = 3584

    try:
        fixed_data = await call_llm_json_with_retry(
            prompt=fix_prompt,
            max_tokens=max_tokens,
            stage_name="Refinement",
        )
        refined_schema = AppSchema(**fixed_data)
        refined_schema.metadata.warnings.append(f"Refinement fixed {len(issues)} issue(s)")
        return refined_schema
    except Exception as e:
        schema.metadata.warnings.append(f"Refinement fallback: {str(e)}")
        return schema
