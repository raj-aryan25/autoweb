"""Assertions shared by controlled progress tests; no production imports of tests."""

from autoweb.login.progress import action_step_ids


def assert_boundaries(events, configuration):
    assert [event.ordinal for event in events] == list(range(1, len(events) + 1))
    elapsed = [event.elapsed_seconds for event in events]
    assert elapsed == sorted(elapsed)
    assert all(event.timestamp.utcoffset() is not None for event in events)
    ids = action_step_ids(configuration.site)
    open_phases = set()
    for event in events:
        if event.kind == "login_outcome":
            continue
        key = (event.kind, event.step_id, event.phase_id)
        if event.status == "started":
            assert key not in open_phases
            open_phases.add(key)
        else:
            assert key in open_phases
            open_phases.remove(key)
        if event.kind == "step":
            assert event.step_id == ids[event.index - 1]
            assert event.action == configuration.site.steps[event.index - 1].action
    assert not open_phases
    assert len([event for event in events if event.kind == "login_outcome"]) == 1


def states(events, kind):
    return [event.status for event in events if event.kind == kind]
