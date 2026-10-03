"""Finite local executor accounting for each physical native bridge request."""
import math
import time
from contextlib import contextmanager


@contextmanager
def native_request_budget(run, deadline_at):
    budget = run.budget
    if budget is None:
        yield deadline_at
        return
    budget.check()
    maximum = min(budget.policy.record["request_timeout_ms"],
        int((min(deadline_at, budget.deadline) - time.time()) * 1000))
    if maximum <= 0:
        budget.block("native bridge request deadline elapsed")
    operation_id = budget.reserve({"executor_slots": 1, "wall_ms": maximum})
    try:
        budget.dispatched(operation_id)
    except BaseException:
        budget.db.release_budget_reservation(budget.account_id, budget.actor, operation_id, **budget.fence)
        raise
    started = time.monotonic()
    try:
        yield min(deadline_at, time.time() + maximum / 1000)
    finally:
        # This slot represents the bounded local request worker. An uncertain
        # native mutation remains in the effect journal and never gets replayed.
        budget.settle(operation_id, {"wall_ms": math.ceil((time.monotonic() - started) * 1000)})
    budget.check()
