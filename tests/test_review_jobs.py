from threading import Event

import pytest

from sp_review_jobs import ReviewJobs


def test_reruns_share_one_job_and_progress_without_blocking():
    jobs = ReviewJobs()
    gate, started = Event(), Event()
    calls = []
    def worker(progress):
        calls.append(1)
        progress("허가서 대조 2/100")
        started.set()
        assert gate.wait(5)
        return {"complete": True}
    try:
        job = jobs.start("same", "doc", worker)
        assert started.wait(2)
        assert jobs.start("same", "doc", worker) is job
        assert job.message == "허가서 대조 2/100" and not job.future.done()
        gate.set()
        assert job.future.result(timeout=2) == {"complete": True}
        assert calls == [1]
    finally:
        gate.set()
        jobs.close()


def test_failed_job_is_not_automatically_retried():
    jobs = ReviewJobs()
    def worker(progress):
        raise ValueError("controlled failure")
    try:
        job = jobs.start("bad", "doc", worker)
        with pytest.raises(ValueError):
            job.future.result(timeout=2)
        assert jobs.start("bad", "doc", worker) is job
        jobs.forget_failed("other", lambda _: False)
        assert jobs.get("bad") is job
        jobs.forget_failed("doc", lambda _: False)
        assert jobs.get("bad") is None
    finally:
        jobs.close()


def test_changed_inputs_have_different_jobs_and_pending_is_not_cleared():
    jobs = ReviewJobs()
    gate = Event()
    try:
        first = jobs.start("v1", "doc", lambda p: gate.wait(5))
        second = jobs.start("v2", "doc", lambda p: "second")
        jobs.forget_failed("doc", lambda _: True)
        assert jobs.get("v1") is first and jobs.get("v2") is second
        gate.set()
        assert second.future.result(timeout=2) == "second"
    finally:
        gate.set()
        jobs.close()
