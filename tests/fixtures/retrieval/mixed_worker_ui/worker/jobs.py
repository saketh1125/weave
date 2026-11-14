from dataclasses import dataclass


@dataclass
class Job:
    id: str
    attempts: int = 0
    status: str = "queued"


def claim_next_job(queue: list[Job]) -> Job | None:
    """Claim the first queued job and increment its retry attempt."""
    for job in queue:
        if job.status == "queued":
            job.status = "running"
            job.attempts += 1
            return job
    return None


def retry_job(job: Job, max_attempts: int = 3) -> str:
    job.status = "queued" if job.attempts < max_attempts else "failed"
    return job.status
