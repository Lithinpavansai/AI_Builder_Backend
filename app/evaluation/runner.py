import asyncio
import os
import json
import time
from datetime import datetime
import httpx
from app.utils.config import config
from app.evaluation.test_prompts import ALL_PROMPTS, LIVE_PROMPTS, DATASET_INFO
from app.pipeline.intent import extract_intent
from app.pipeline.system_design import design_system
from app.pipeline.schema_gen import generate_schemas
from app.pipeline.refinement import refine_schema
from app.validators.validator import validate_app_schema
from app.validators.runtime_validator import validate_runtime
from app.validators.models import AppSchema

RESULTS_FILE = os.path.join(os.path.dirname(__file__), "results.json")
DELAY_BETWEEN_PROMPTS = 2  # seconds between prompts
DEPLOYED_API_URL = "https://app-compiler-api.onrender.com"


async def run_single_prompt(prompt_data: dict) -> dict:
    start_time = time.time()
    iso_timestamp = datetime.utcnow().isoformat()
    result = {
        "id": prompt_data["id"],
        "category": prompt_data["category"],
        "difficulty": prompt_data["difficulty"],
        "prompt": prompt_data["prompt"],
        "status": "failed",
        "latency_seconds": 0.0,
        "runtime_score": 0,
        "executable": False,
        "checks": {
            "route_completeness": False,
            "auth_coverage": False,
            "db_coverage": False,
            "role_consistency": False,
            "foreign_key_validity": False,
            "business_rule_routes": False,
        },
        "model": config.GROQ_MODEL,
        "timestamp": iso_timestamp,
        "pages_generated": 0,
        "endpoints_generated": 0,
        "tables_generated": 0,
        "roles_generated": 0,
        "assumptions_made": 0,
        "warnings_count": 0,
        "failure_reason": None,
        "failure_type": None,
        "expected_behavior": prompt_data.get("expected_behavior", "n/a"),
    }

    try:
        # Stage 1: Intent Extraction
        intent = await extract_intent(prompt_data["prompt"])
        result["assumptions_made"] = len(intent.assumptions)

        # Stage 2: System Design
        design = await design_system(intent)

        # Stage 3: Schema Generation
        schema = await generate_schemas(intent, design)

        # Stage 4: Refinement
        refined = await refine_schema(schema)

        # Validation
        report, _ = validate_app_schema(refined.model_dump())
        result["warnings_count"] = len(refined.metadata.warnings)

        # Runtime validation (6 structural checks)
        runtime = validate_runtime(refined)

        # Extract per-check pass/fail
        checks_dict = {}
        for c in runtime.get("checks", []):
            checks_dict[c["check"]] = bool(c["passed"])

        # Populate results
        result["status"] = "success"
        result["runtime_score"] = int(runtime["score"])
        result["executable"] = bool(runtime["executable"])
        result["checks"] = checks_dict
        result["pages_generated"] = len(refined.ui.pages)
        result["endpoints_generated"] = len(refined.api.endpoints)
        result["tables_generated"] = len(refined.database.tables)
        result["roles_generated"] = len(refined.auth.roles)

    except Exception as e:
        error_str = str(e)
        result["failure_reason"] = error_str[:300]

        # Classify failure type
        if "429" in error_str or "rate_limit" in error_str.lower():
            result["failure_type"] = "rate_limit"
        elif "json" in error_str.lower() or "invalid" in error_str.lower():
            result["failure_type"] = "invalid_json"
        elif "timeout" in error_str.lower():
            result["failure_type"] = "timeout"
        elif "validation" in error_str.lower():
            result["failure_type"] = "validation_error"
        else:
            result["failure_type"] = "unknown"

    result["latency_seconds"] = round(time.time() - start_time, 2)
    return result


async def run_live_prompt_against_deployment(prompt_data: dict, live_url: str = DEPLOYED_API_URL) -> dict:
    start_time = time.time()
    iso_timestamp = datetime.utcnow().isoformat()
    result = {
        "id": prompt_data["id"],
        "category": prompt_data["category"],
        "difficulty": prompt_data["difficulty"],
        "prompt": prompt_data["prompt"],
        "status": "failed",
        "latency_seconds": 0.0,
        "runtime_score": 0,
        "executable": False,
        "checks": {
            "route_completeness": False,
            "auth_coverage": False,
            "db_coverage": False,
            "role_consistency": False,
            "foreign_key_validity": False,
            "business_rule_routes": False,
        },
        "model": f"{config.GROQ_MODEL} (Live: {live_url})",
        "live_url": live_url,
        "timestamp": iso_timestamp,
        "pages_generated": 0,
        "endpoints_generated": 0,
        "tables_generated": 0,
        "roles_generated": 0,
        "assumptions_made": 0,
        "warnings_count": 0,
        "failure_reason": None,
        "failure_type": None,
        "expected_behavior": prompt_data.get("expected_behavior", "n/a"),
    }

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.post(
                f"{live_url.rstrip('/')}/api/generate",
                json={"prompt": prompt_data["prompt"]},
            )
            
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            
            payload = resp.json()
            schema_data = payload.get("schema")
            if not schema_data:
                raise ValueError(f"No schema returned: {payload.get('error', 'Unknown error')}")
            
            app_schema = AppSchema.model_validate(schema_data)
            runtime = validate_runtime(app_schema)

            checks_dict = {}
            for c in runtime.get("checks", []):
                checks_dict[c["check"]] = bool(c["passed"])

            result["status"] = "success"
            result["runtime_score"] = int(runtime["score"])
            result["executable"] = bool(runtime["executable"])
            result["checks"] = checks_dict
            result["pages_generated"] = len(app_schema.ui.pages)
            result["endpoints_generated"] = len(app_schema.api.endpoints)
            result["tables_generated"] = len(app_schema.database.tables)
            result["roles_generated"] = len(app_schema.auth.roles)
            result["warnings_count"] = len(app_schema.metadata.warnings)
            result["assumptions_made"] = len(app_schema.metadata.assumptions)

    except Exception as e:
        error_str = str(e)
        result["failure_reason"] = error_str[:300]
        if "timeout" in error_str.lower():
            result["failure_type"] = "timeout"
        else:
            result["failure_type"] = "deployment_api_error"

    result["latency_seconds"] = round(time.time() - start_time, 2)
    return result


async def run_evaluation(prompt_ids: list = None, run_live: bool = True) -> dict:
    """
    Run evaluation on the 23 prompts locally, plus optional live deployed verification.
    Computes all statistics programmatically in code.
    """
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass

    prompts_to_run = ALL_PROMPTS
    if prompt_ids:
        prompts_to_run = [p for p in ALL_PROMPTS if p["id"] in prompt_ids]

    print("\n" + "=" * 60)
    print("SCHEMA FORGE — 23-PROMPT EVALUATION BENCHMARK")
    print("=" * 60)
    print(f"Model ID: {config.GROQ_MODEL}")
    print(f"Total Prompts: {len(prompts_to_run)}")
    print(f"Delay Between Prompts: {DELAY_BETWEEN_PROMPTS}s")
    print("=" * 60 + "\n")

    results = []
    eval_start = time.time()

    for i, prompt_data in enumerate(prompts_to_run):
        print(f"[{i+1}/{len(prompts_to_run)}] Running {prompt_data['id']} ({prompt_data['category']})...", flush=True)
        res = await run_single_prompt(prompt_data)
        results.append(res)

        status_tag = "[OK]" if res["status"] == "success" else "[FAIL]"
        print(
            f"  {status_tag} {res['status']} | {res['latency_seconds']}s | "
            f"Score: {res['runtime_score']}/100 | Executable: {res['executable']} | "
            f"Pages: {res['pages_generated']} | Endpoints: {res['endpoints_generated']} | Tables: {res['tables_generated']}",
            flush=True,
        )

        if res["failure_reason"]:
            print(f"  [!] {res['failure_type']}: {res['failure_reason'][:120]}...", flush=True)

        if i < len(prompts_to_run) - 1:
            await asyncio.sleep(DELAY_BETWEEN_PROMPTS)

    total_time = round(time.time() - eval_start, 2)

    # Run the 3 live prompts against live deployed API
    live_results = []
    if run_live:
        print("\n" + "=" * 60)
        print(f"RUNNING LIVE DEPLOYMENT EVALUATION ({DEPLOYED_API_URL})")
        print("=" * 60)
        for i, live_p in enumerate(LIVE_PROMPTS):
            print(f"[Live {i+1}/{len(LIVE_PROMPTS)}] Running {live_p['id']} against {DEPLOYED_API_URL}...", flush=True)
            live_res = await run_live_prompt_against_deployment(live_p, DEPLOYED_API_URL)
            live_results.append(live_res)
            status_tag = "[OK]" if live_res["status"] == "success" else "[FAIL]"
            print(
                f"  {status_tag} {live_res['status']} | {live_res['latency_seconds']}s | "
                f"Score: {live_res['runtime_score']}/100 | Executable: {live_res['executable']}",
                flush=True,
            )
            if i < len(LIVE_PROMPTS) - 1:
                await asyncio.sleep(2)

    # Code-computed summary statistics (no hand calculations)
    successful = [r for r in results if r["status"] == "success"]
    failed = [r for r in results if r["status"] == "failed"]

    completion_rate_pct = round((len(successful) / len(results)) * 100, 1) if results else 0.0
    mean_score = round(sum(r["runtime_score"] for r in results) / len(results), 2) if results else 0.0
    mean_latency = round(sum(r["latency_seconds"] for r in results) / len(results), 2) if results else 0.0

    normal_results = [r for r in results if r["difficulty"] == "normal"]
    edge_results = [r for r in results if r["difficulty"] == "edge_case"]
    live_style_results = [r for r in results if r["difficulty"] == "live_deployment"]

    normal_success = len([r for r in normal_results if r["status"] == "success"])
    edge_success = len([r for r in edge_results if r["status"] == "success"])
    live_style_success = len([r for r in live_style_results if r["status"] == "success"])

    normal_mean_score = round(sum(r["runtime_score"] for r in normal_results) / len(normal_results), 2) if normal_results else 0.0
    edge_mean_score = round(sum(r["runtime_score"] for r in edge_results) / len(edge_results), 2) if edge_results else 0.0
    live_style_mean_score = round(sum(r["runtime_score"] for r in live_style_results) / len(live_style_results), 2) if live_style_results else 0.0

    summary = {
        "evaluation_date": datetime.utcnow().isoformat(),
        "model": config.GROQ_MODEL,
        "total_prompts": len(results),
        "successful": len(successful),
        "failed": len(failed),
        "completion_rate": f"{completion_rate_pct}%",
        "mean_runtime_score": mean_score,
        "mean_latency_seconds": mean_latency,
        "normal_prompts_count": len(normal_results),
        "normal_success_count": normal_success,
        "normal_mean_score": normal_mean_score,
        "edge_case_prompts_count": len(edge_results),
        "edge_case_success_count": edge_success,
        "edge_case_mean_score": edge_mean_score,
        "live_style_prompts_count": len(live_style_results),
        "live_style_success_count": live_style_success,
        "live_style_mean_score": live_style_mean_score,
        "total_evaluation_time_minutes": round(total_time / 60, 2),
        "dataset_version": "23-prompt-dataset-v1.0"
    }

    final_report = {
        "summary": summary,
        "results": results,
        "live_deployment_verification": {
            "deployed_url": DEPLOYED_API_URL,
            "results": live_results
        }
    }

    # Save to results.json
    with open(RESULTS_FILE, "w", encoding="utf-8") as f:
        json.dump(final_report, f, indent=2)

    # Print summary
    print("\n" + "=" * 60)
    print("EVALUATION BENCHMARK COMPLETE")
    print("=" * 60)
    print(f"Model: {summary['model']}")
    print(f"Total Prompts: {summary['total_prompts']} | Successful: {summary['successful']} | Failed: {summary['failed']}")
    print(f"Completion Rate: {summary['completion_rate']}")
    print(f"Mean Runtime Validator Score: {summary['mean_runtime_score']} / 100")
    print(f"Mean Latency: {summary['mean_latency_seconds']}s")
    print(f"Normal Mean Score: {summary['normal_mean_score']} / 100 (Success: {normal_success}/{len(normal_results)})")
    print(f"Edge Case Mean Score: {summary['edge_mean_score']} / 100 (Success: {edge_success}/{len(edge_results)})")
    print(f"Live Style Mean Score: {summary['live_style_mean_score']} / 100 (Success: {live_style_success}/{len(live_style_results)})")
    print(f"Results written to: {RESULTS_FILE}")
    print("=" * 60 + "\n")

    return final_report


if __name__ == "__main__":
    asyncio.run(run_evaluation())

