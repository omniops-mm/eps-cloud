"""Pins the streak and tracker rules.

Each test writes a short history, recomputes, and asserts the resulting state.
The habit patterns read left to right one character per day: P passed, F failed,
. no entry at all.
"""

from app.recompute import recompute_streak_state, recompute_tracker_state
from tests.conftest import days


def test_no_history_is_a_zero_streak(session, build):
    streak_id = build.streak()

    state = recompute_streak_state(session, streak_id, today=days(0))

    assert state.current_streak == 0
    assert state.personal_record == 0
    assert state.last_grace_used_date is None


def test_consecutive_passes_extend_the_streak(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPP")

    state = recompute_streak_state(session, streak_id, today=days(2))

    assert state.current_streak == 3


def test_an_explicit_failure_resets_the_streak(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPF")

    state = recompute_streak_state(session, streak_id, today=days(3))

    assert state.current_streak == 0


def test_a_past_day_with_no_entry_counts_as_a_failure(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPP.P")

    state = recompute_streak_state(session, streak_id, today=days(4))

    assert state.current_streak == 1


def test_today_with_no_entry_is_not_yet_a_failure(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPP")

    state = recompute_streak_state(session, streak_id, today=days(3))

    assert state.current_streak == 3


def test_a_pass_logged_today_counts_immediately(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPP")

    state = recompute_streak_state(session, streak_id, today=days(3))

    assert state.current_streak == 4


def test_a_bad_day_forgives_a_missing_entry(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPP.P")
    build.bad_day(days(3))

    state = recompute_streak_state(session, streak_id, today=days(4))

    assert state.current_streak == 4


def test_a_bad_day_does_not_forgive_an_explicit_failure(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPFP")
    build.bad_day(days(3))

    state = recompute_streak_state(session, streak_id, today=days(4))

    assert state.current_streak == 1


def test_grace_is_unavailable_below_the_minimum_streak(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPPPF")

    state = recompute_streak_state(session, streak_id, today=days(6))

    assert state.current_streak == 0
    assert state.last_grace_used_date is None


def test_grace_absorbs_a_failure_at_the_minimum_streak(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPPPPF")

    state = recompute_streak_state(session, streak_id, today=days(7))

    assert state.current_streak == 7
    assert state.last_grace_used_date == days(7)


def test_grace_holds_the_streak_rather_than_extending_it(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPPPPFP")

    state = recompute_streak_state(session, streak_id, today=days(8))

    assert state.current_streak == 8


def test_grace_also_covers_a_missing_entry(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPPPP.P")

    state = recompute_streak_state(session, streak_id, today=days(8))

    assert state.current_streak == 8
    assert state.last_grace_used_date == days(7)


def test_a_second_failure_inside_the_cooldown_resets_fully(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPPPPFPPF")

    state = recompute_streak_state(session, streak_id, today=days(10))

    assert state.current_streak == 0


def test_grace_is_available_again_once_the_cooldown_has_passed(session, build):
    streak_id = build.streak()
    # grace spent on day 7, next failure on day 14 is exactly the cooldown later
    build.log(streak_id, "PPPPPPPFPPPPPPF")

    state = recompute_streak_state(session, streak_id, today=days(14))

    assert state.current_streak == 13
    assert state.last_grace_used_date == days(14)


def test_disabled_grace_means_any_failure_resets(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPPPPF")

    state = recompute_streak_state(session, streak_id, today=days(7), grace_enabled=False)

    assert state.current_streak == 0
    assert state.last_grace_used_date is None


def test_disabled_grace_still_forgives_bad_days(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPPPP.P")
    build.bad_day(days(7))

    state = recompute_streak_state(session, streak_id, today=days(8), grace_enabled=False)

    assert state.current_streak == 8


def test_the_personal_record_tracks_the_best_run(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPPFPP")

    state = recompute_streak_state(session, streak_id, today=days(7))

    assert state.current_streak == 2
    assert state.personal_record == 5


def test_the_personal_record_survives_a_reset(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPPFPP")
    recompute_streak_state(session, streak_id, today=days(7))

    build.log(streak_id, "F", start=days(8))
    state = recompute_streak_state(session, streak_id, today=days(8))

    assert state.current_streak == 0
    assert state.personal_record == 5


def test_the_personal_record_never_decreases_after_a_correction(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPPPP")
    recompute_streak_state(session, streak_id, today=days(4))

    # the third day turns out to have been a failure after all
    build.edit(streak_id, days(2), passed=False)
    state = recompute_streak_state(session, streak_id, today=days(4))

    assert state.current_streak == 2
    assert state.personal_record == 5


def test_a_retroactive_fix_repairs_the_streak(session, build):
    streak_id = build.streak()
    build.log(streak_id, "PPFPP")
    before = recompute_streak_state(session, streak_id, today=days(4))
    assert before.current_streak == 2

    build.edit(streak_id, days(2), passed=True)
    state = recompute_streak_state(session, streak_id, today=days(4))

    assert state.current_streak == 5


def test_a_tracker_never_done_reports_nothing(session, build):
    tracker_id = build.tracker()

    state = recompute_tracker_state(session, tracker_id, today=days(0))

    assert state.days_since_last_done == 0
    assert state.last_done_date is None


def test_a_tracker_counts_days_since_the_last_time_done(session, build):
    tracker_id = build.tracker()
    build.done(tracker_id, days(0), days(2))

    state = recompute_tracker_state(session, tracker_id, today=days(5))

    assert state.days_since_last_done == 3
    assert state.last_done_date == days(2)


def test_a_tracker_done_today_is_at_zero(session, build):
    tracker_id = build.tracker()
    build.done(tracker_id, days(4))

    state = recompute_tracker_state(session, tracker_id, today=days(4))

    assert state.days_since_last_done == 0


def test_a_tracker_ignores_events_that_were_not_done(session, build):
    tracker_id = build.tracker()
    build.done(tracker_id, days(1))
    build.done(tracker_id, days(4), activity_done=False)

    state = recompute_tracker_state(session, tracker_id, today=days(5))

    assert state.days_since_last_done == 4
    assert state.last_done_date == days(1)


def test_a_tracker_picks_up_a_retroactively_added_day(session, build):
    tracker_id = build.tracker()
    build.done(tracker_id, days(1))
    recompute_tracker_state(session, tracker_id, today=days(5))

    build.done(tracker_id, days(3))
    state = recompute_tracker_state(session, tracker_id, today=days(5))

    assert state.days_since_last_done == 2
