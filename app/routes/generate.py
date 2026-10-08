import asyncio
from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from app.utils.job_store import create_job, update_job, get_job, complete_job, fail_job
from app.validators.models import GenerateRequest
from app.validators.orchestrator import run_pipeline
from app.pipeline.intent import extract_intent
from app.pipeline.system_design import design_system
from app.pipeline.schema_gen import generate_schemas
from app.pipeline.refinement import refine_schema
from app.validators.validator import validate_app_schema
from app.validators.repair import run_with_repair
from app.validators.runtime_validator import validate_runtime
from app.evaluation.test_prompts import ALL_PROMPTS, DATASET_INFO

router = APIRouter()


# --- BACKGROUND PIPELINE TASK ---

async def run_pipeline_background(job_id: str, prompt: str):
    import time
    start_time = time.time()
    try:
        update_job(job_id, status="processing", current_stage="intent_extraction", progress=10)
        from app.pipeline.intent import extract_intent
        intent = await extract_intent(prompt)

        update_job(job_id, current_stage="system_design", progress=30)
        from app.pipeline.system_design import design_system
        design = await design_system(intent)

        update_job(job_id, current_stage="schema_generation", progress=50)
        from app.pipeline.schema_gen import generate_schemas
        schema = await generate_schemas(intent, design)

        update_job(job_id, current_stage="refinement", progress=85)
        from app.pipeline.refinement import refine_schema
        from app.validators.validator import validate_app_schema
        refined = await refine_schema(schema)

        update_job(job_id, current_stage="validation", progress=95)
        report, _ = validate_app_schema(refined.model_dump())

        result = {
            "status": "success",
            "schema": refined.model_dump(),
            "validation_report": report.model_dump(),
            "all_valid": report.is_valid,
            "prompt_received": prompt,
            "pipeline_version": "1.0.0"
        }
        complete_job(job_id, result)

        latency_sec = round(time.time() - start_time, 2)
        update_job(job_id, latency_seconds=latency_sec)

    except Exception as e:
        import traceback
        traceback.print_exc()
        latency_sec = round(time.time() - start_time, 2)
        fail_job(job_id, str(e))
        update_job(job_id, latency_seconds=latency_sec)
    finally:
        from app.utils.llm import job_token_tracker
        s3_reasons = job_token_tracker.stage_3_finish_reasons
        s3_reasons_str = f"Call A: {s3_reasons.get('Call A', 'N/A')}, Call B: {s3_reasons.get('Call B', 'N/A')}"
        print(
            f"\n[JOB SUMMARY] Job: {job_id} | "
            f"Prompt Tokens: {job_token_tracker.prompt_tokens} | "
            f"Completion Tokens: {job_token_tracker.completion_tokens} | "
            f"Total Tokens: {job_token_tracker.total_tokens} | "
            f"LLM Calls: {job_token_tracker.total_calls} | "
            f"JSON Repairs: {job_token_tracker.total_repair_count} | "
            f"Normalizations: {job_token_tracker.total_normalizations} | "
            f"Total Sleep Time: {job_token_tracker.total_sleep_seconds:.1f}s | "
            f"Stage 3 Finish Reasons: [{s3_reasons_str}]\n",
            flush=True
        )


# --- MAIN PRODUCTION ROUTE ---

@router.post("/generate", tags=["Generate"])
async def generate(request: GenerateRequest):
    """
    Main production endpoint.
    Runs the full 4-stage pipeline with validation, repair, and retry orchestration.

    Stages:
    1. Intent Extraction
    2. System Design
    3. Schema Generation (UI + API + DB + Auth + Business Logic)
    4. Refinement (cross-layer consistency)
    """
    import time
    start_time = time.time()
    try:
        result = await run_pipeline(request.prompt)
        result["prompt_received"] = request.prompt
        result["pipeline_version"] = "1.0.0"

        return JSONResponse(status_code=200, content=result)
    except Exception as e:
        import traceback
        traceback.print_exc()
        return JSONResponse(status_code=500, content={
            "status": "failed",
            "error": str(e),
            "prompt_received": request.prompt,
            "pipeline_version": "1.0.0"
        })
    finally:
        from app.utils.llm import job_token_tracker
        s3_reasons = job_token_tracker.stage_3_finish_reasons
        s3_reasons_str = f"Call A: {s3_reasons.get('Call A', 'N/A')}, Call B: {s3_reasons.get('Call B', 'N/A')}"
        print(
            f"\n[JOB SUMMARY] "
            f"Prompt Tokens: {job_token_tracker.prompt_tokens} | "
            f"Completion Tokens: {job_token_tracker.completion_tokens} | "
            f"Total Tokens: {job_token_tracker.total_tokens} | "
            f"LLM Calls: {job_token_tracker.total_calls} | "
            f"JSON Repairs: {job_token_tracker.total_repair_count} | "
            f"Normalizations: {job_token_tracker.total_normalizations} | "
            f"Total Sleep Time: {job_token_tracker.total_sleep_seconds:.1f}s | "
            f"Stage 3 Finish Reasons: [{s3_reasons_str}]\n",
            flush=True
        )


# --- PIPELINE INFO ROUTE ---

@router.get("/generate/info", tags=["Generate"])
async def pipeline_info():
    """Returns pipeline configuration info without making any LLM calls."""
    return {
        "pipeline_version": "1.0.0",
        "stages": [
            {"stage": 1, "name": "Intent Extraction", "model": "llama-3.3-70b-versatile"},
            {"stage": 2, "name": "System Design", "model": "llama-3.3-70b-versatile"},
            {"stage": 3, "name": "Schema Generation", "model": "llama-3.3-70b-versatile", "llm_calls": 5},
            {"stage": 4, "name": "Refinement", "model": "llama-3.3-70b-versatile"}
        ],
        "total_llm_calls_per_request": 8,
        "max_pipeline_attempts": 3,
        "max_repair_attempts_per_stage": 3,
        "output_layers": ["ui", "api", "database", "auth", "business_logic"],
        "validation": "pydantic v2",
        "llm_provider": "groq"
    }


# --- DEV/TEST ROUTES ---

@router.post("/dev/test-intent", tags=["Dev"])
async def test_intent(request: GenerateRequest):
    """Dev route: Test Stage 1 only - Intent Extraction"""
    try:
        intent = await extract_intent(request.prompt)
        return intent.model_dump()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/dev/test-system-design", tags=["Dev"])
async def test_system_design(request: GenerateRequest):
    """Dev route: Test Stages 1+2 - Intent + System Design"""
    try:
        intent = await extract_intent(request.prompt)
        design = await design_system(intent)
        return design.model_dump()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/dev/test-schema-gen", tags=["Dev"])
async def test_schema_gen(request: GenerateRequest):
    """Dev route: Test Stages 1+2+3 - Full schema without refinement"""
    try:
        intent = await extract_intent(request.prompt)
        design = await design_system(intent)
        schema = await generate_schemas(intent, design)
        return schema.model_dump()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/dev/test-refinement", tags=["Dev"])
async def test_refinement(request: GenerateRequest):
    """Dev route: Test full pipeline including refinement"""
    try:
        intent = await extract_intent(request.prompt)
        design = await design_system(intent)
        schema = await generate_schemas(intent, design)
        refined = await refine_schema(schema)
        return refined.model_dump()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/dev/validate", tags=["Dev"])
async def validate_route(request: GenerateRequest):
    """Dev route: Run pipeline and return validation report"""
    try:
        intent = await extract_intent(request.prompt)
        design = await design_system(intent)
        schema = await generate_schemas(intent, design)
        report, _ = validate_app_schema(schema.model_dump())
        return report.model_dump()
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@router.post("/dev/generate-with-repair", tags=["Dev"])
async def generate_with_repair(request: GenerateRequest):
    """Dev route: Run pipeline with repair, return schema + all validation reports"""
    try:
        schema, reports = await run_with_repair(request.prompt)
        return {
            "schema": schema.model_dump(),
            "validation_reports": [r.model_dump() for r in reports],
            "repair_triggered": any("repair" in r.stage for r in reports),
            "all_valid": all(r.is_valid for r in reports)
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# --- ASYNC JOB ROUTES ---

@router.post("/generate/async", tags=["Generate"])
async def generate_async(request: GenerateRequest):
    """
    Async version of generate. Returns job_id immediately.
    Poll /status/{job_id} for progress.
    Fetch /result/{job_id} when complete.
    """
    job_id = create_job(request.prompt)
    asyncio.create_task(run_pipeline_background(job_id, request.prompt))
    return {
        "job_id": job_id,
        "status": "created",
        "message": "Pipeline started. Poll /api/status/{job_id} for progress.",
        "status_url": f"/api/status/{job_id}",
        "result_url": f"/api/result/{job_id}"
    }


@router.get("/status/{job_id}", tags=["Generate"])
async def get_status(job_id: str):
    """Check pipeline progress for a job."""
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": f"Job {job_id} not found"})
    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "current_stage": job["current_stage"],
        "progress": job["progress"],
        "created_at": job["created_at"],
        "completed_at": job["completed_at"],
        "latency_seconds": job.get("latency_seconds"),
        "error": job["error"]
    }


@router.get("/result/{job_id}", tags=["Generate"])
async def get_result(job_id: str):
    """Fetch the final result for a completed job."""
    job = get_job(job_id)
    if not job:
        return JSONResponse(status_code=404, content={"error": f"Job {job_id} not found"})
    if job["status"] in ("processing", "created"):
        return JSONResponse(status_code=202, content={
            "job_id": job_id,
            "status": job["status"],
            "current_stage": job["current_stage"],
            "progress": job["progress"],
            "latency_seconds": job.get("latency_seconds"),
            "message": "Pipeline still running. Try again shortly."
        })
    if job["status"] == "failed":
        return JSONResponse(status_code=500, content={
            "job_id": job_id,
            "status": "failed",
            "latency_seconds": job.get("latency_seconds"),
            "error": job["error"]
        })
    return JSONResponse(status_code=200, content={
        "job_id": job_id,
        "status": "completed",
        "completed_at": job["completed_at"],
        "latency_seconds": job.get("latency_seconds"),
        "result": job["result"]
    })


@router.post("/runtime-validate", tags=["Generate"])
async def runtime_validate(request: GenerateRequest):
    """
    Run pipeline then validate if output schema is executable.
    Returns a runtime validation report with score out of 100.
    """
    try:
        intent = await extract_intent(request.prompt)
        design = await design_system(intent)
        schema = await generate_schemas(intent, design)
        report = validate_runtime(schema)
        return {
            "prompt": request.prompt,
            "app_name": schema.app_name,
            "runtime_validation": report
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


# --- EVALUATION ROUTES ---

@router.get("/eval/dataset", tags=["Evaluation"])
async def get_dataset():
    """Returns the evaluation dataset info and all test prompts."""
    return {
        "dataset_info": DATASET_INFO,
        "prompts": ALL_PROMPTS
    }

@router.post("/eval/run-single/{prompt_id}", tags=["Evaluation"])
async def run_single_eval(prompt_id: str):
    """Run evaluation on a single prompt by ID (e.g. N01, E03)"""
    from app.evaluation.runner import run_single_prompt
    prompt_data = next((p for p in ALL_PROMPTS if p["id"] == prompt_id), None)
    if not prompt_data:
        return JSONResponse(status_code=404, content={"error": f"Prompt {prompt_id} not found"})
    try:
        result = await run_single_prompt(prompt_data)
        return result
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})

@router.get("/eval/results", tags=["Evaluation"])
async def get_eval_results():
    """Returns the latest evaluation results if they exist."""
    import os
    results_path = "app/evaluation/results.json"
    if not os.path.exists(results_path):
        return JSONResponse(status_code=404, content={
            "error": "No evaluation results found. Run the evaluation first."
        })
    with open(results_path) as f:
        import json
        return json.load(f)

@router.get("/eval/report", tags=["Evaluation"])
async def get_eval_report():
    """Returns formatted evaluation metrics report."""
    import os, json
    results_path = "app/evaluation/results.json"
    if not os.path.exists(results_path):
        return JSONResponse(status_code=404, content={
            "error": "No results yet. Run evaluation first via POST /eval/run-single/{id}"
        })
    with open(results_path) as f:
        data = json.load(f)

    summary = data["summary"]
    results = data["results"]

    normal = [r for r in results if r["difficulty"] == "normal"]
    edge = [r for r in results if r["difficulty"] == "edge_case"]

    return {
        "report_title": "App Compiler Evaluation Report",
        "generated_at": summary["evaluation_date"],
        "overview": {
            "total_prompts_tested": summary["total_prompts"],
            "overall_success_rate": summary["success_rate"],
            "normal_prompts_success_rate": summary["normal_success_rate"],
            "edge_case_success_rate": summary["edge_case_success_rate"],
            "avg_latency_seconds": summary["avg_latency_seconds"],
            "avg_runtime_score": summary["avg_runtime_score"],
            "total_evaluation_time_minutes": summary["total_evaluation_time_minutes"]
        },
        "failure_analysis": {
            "total_failures": summary["failed"],
            "failure_breakdown": summary["failure_types"],
            "failed_prompts": [
                {"id": r["id"], "category": r["category"], "reason": r["failure_type"]}
                for r in results if r["status"] == "failed"
            ]
        },
        "performance_by_category": {
            cat: {
                "total": len([r for r in results if r["category"] == cat]),
                "success": len([r for r in results if r["category"] == cat and r["status"] == "success"]),
                "avg_score": round(
                    sum(r["runtime_score"] for r in results if r["category"] == cat and r["status"] == "success") /
                    max(len([r for r in results if r["category"] == cat and r["status"] == "success"]), 1), 1
                )
            }
            for cat in set(r["category"] for r in results)
        },
        "detailed_results": results
    }
