from typing import Any, Literal
from pydantic import BaseModel, ConfigDict


class JobValidation(BaseModel):
    model_config = ConfigDict(frozen=True)
    state: Literal["verified", "partial_verified", "rejected"]
    reason_codes: tuple[str, ...] = ()


def validate_job(job: dict[str, Any], selected_url: str) -> JobValidation:
    hard, partial, info = [], [], []
    if str(job.get("page_type") or "") not in {"", "job_detail", "job_description"}: hard.append("not_job_detail_page")
    if job.get("validation_state") == "rejected": hard.append("extraction_rejected")
    for field in ("title", "source_url"):
        if not isinstance(job.get(field), str) or not job[field].strip(): hard.append(f"missing_{field}")
    if not isinstance(job.get("location"), str) or not job["location"].strip(): partial.append("missing_location")
    missing = [f"missing_{field}" for field in ("responsibilities", "requirements") if not isinstance(job.get(field), list) or not job[field] or any(not isinstance(x, str) or not x.strip() for x in job[field])]
    (hard if len(missing) == 2 else partial).extend(missing)
    if not isinstance(job.get("company"), str) or not job["company"].strip():
        (partial if missing else info).append("missing_company" if missing else "company_not_disclosed")
    if job.get("source_url") != selected_url: hard.append("source_url_mismatch")
    return JobValidation(state="rejected" if hard else ("partial_verified" if partial else "verified"), reason_codes=tuple(dict.fromkeys(hard or partial or info)))
