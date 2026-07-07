import pytest

from hive.jobs import InvalidTransition, Job, JobState, transition


def make_job() -> Job:
    return Job(workflow="w", adapter="a")


def test_happy_path():
    job = make_job()
    for state in (
        JobState.PLANNING,
        JobState.EXECUTING,
        JobState.REVIEWING,
        JobState.AWAITING_APPROVAL,
        JobState.DONE,
    ):
        transition(job, state)
    assert job.state is JobState.DONE


def test_illegal_jump_raises():
    job = make_job()
    with pytest.raises(InvalidTransition):
        transition(job, JobState.DONE)  # queued -> done is not a thing


def test_done_is_terminal():
    job = make_job()
    transition(job, JobState.CANCELLED)
    with pytest.raises(InvalidTransition):
        transition(job, JobState.PLANNING)


def test_rejection_path():
    job = make_job()
    transition(job, JobState.PLANNING)
    transition(job, JobState.EXECUTING)
    transition(job, JobState.REVIEWING)
    transition(job, JobState.AWAITING_APPROVAL)
    transition(job, JobState.CANCELLED)
    assert job.state is JobState.CANCELLED
