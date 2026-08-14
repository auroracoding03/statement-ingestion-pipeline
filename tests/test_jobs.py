"""In-process job registry progress updates."""

from src.api import jobs


def test_job_progress_is_stored_and_returned():
    job_id = jobs.create_job("ai-analyze")
    created = jobs.get_job(job_id)
    assert created is not None
    assert created["status"] == "pending"
    assert created["progress"] is None

    jobs.set_progress(job_id, 3, 8, "Merchant profiles 3/8")
    running = jobs.get_job(job_id)
    assert running is not None
    assert running["progress"] == {
        "current": 3,
        "total": 8,
        "message": "Merchant profiles 3/8",
    }
