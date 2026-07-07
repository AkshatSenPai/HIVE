from hive.artifacts import Brief, Draft
from hive.jobs.store import JobStore


def test_save_and_list_artifacts(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    brief = Brief(subject="x", summary="s", job_id="job_1", produced_by="research")
    draft = Draft(kind="proposal", title="t", body="b", job_id="job_1", produced_by="maker")
    store.save_artifact("job_1", brief)
    store.save_artifact("job_1", draft)
    store.save_artifact("job_2", Brief(subject="y", summary="s2", job_id="job_2"))

    got = store.list_artifacts("job_1")
    assert {a["artifact_type"] for a in got} == {"Brief", "Draft"}
    draft = next(a for a in got if a["artifact_type"] == "Draft")
    assert draft["body"] == "b"
    assert draft["kind"] == "proposal"  # the Draft's own 'kind' field survives intact
    assert len(store.list_artifacts("job_2")) == 1


def test_get_single_artifact(tmp_path):
    store = JobStore(tmp_path / "jobs.sqlite3")
    brief = Brief(subject="x", summary="s", job_id="job_1")
    store.save_artifact("job_1", brief)
    fetched = store.get_artifact(brief.id)
    assert fetched["artifact_type"] == "Brief"
    assert fetched["summary"] == "s"
    assert store.get_artifact("nope") is None
