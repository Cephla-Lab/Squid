"""One owner of the stage's motion at a time (squid.stage.reservation): whoever takes the reservation first drives the
stage, everyone else is refused with what holds it, whichever thread they ask from."""

import threading

import pytest

from squid.stage.reservation import StageMotionRefused, StageMotionReservation


def test_a_second_owner_is_refused_with_what_holds_the_stage():
    reservation = StageMotionReservation()
    reservation.take("an acquisition is running")

    with pytest.raises(StageMotionRefused) as refused:
        reservation.take("a stage move from the control server is running")

    assert refused.value.reason == "an acquisition is running"
    assert str(refused.value) == "Refused: an acquisition is running"
    assert reservation.holder == "an acquisition is running"


def test_the_stage_is_free_again_once_its_owner_releases_it():
    reservation = StageMotionReservation()
    hold = reservation.take("an acquisition is running")
    hold.release()

    assert reservation.holder is None
    reservation.take("a move to the loading position is running")


def test_a_second_release_does_not_free_the_stage_from_its_next_owner():
    reservation = StageMotionReservation()
    first = reservation.take("an acquisition is running")
    first.release()
    second = reservation.take("a move to the loading position is running")

    first.release()

    assert first.released and not second.released
    assert reservation.holder == "a move to the loading position is running"


def test_what_holds_the_stage_can_be_read_when_asked():
    """A loading move holds the stage while it moves there, while the stage stays there and while it comes back: the
    refusal names the stage the moment it is refused."""
    reservation = StageMotionReservation()
    where = ["a move to the loading position is running"]
    reservation.take(lambda: where[0])

    where[0] = "the stage is at the loading position (return from it first)"

    with pytest.raises(StageMotionRefused, match="the stage is at the loading position"):
        reservation.take("an acquisition is running")


def test_of_competing_owners_exactly_one_gets_the_stage():
    reservation = StageMotionReservation()
    contenders = 8
    start = threading.Barrier(contenders)
    won, refused = [], []

    def contend(i):
        start.wait()
        try:
            reservation.take(f"owner {i}")
            won.append(i)
        except StageMotionRefused as e:
            refused.append(e.reason)

    threads = [threading.Thread(target=contend, args=(i,)) for i in range(contenders)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    assert len(won) == 1
    assert refused == [f"owner {won[0]}"] * (contenders - 1)


# --- the observer (on_holder_changed): the control panel lock follows it ---------------------------------------------


def _observed(reservation):
    seen = []
    reservation.on_holder_changed = seen.append
    return seen


def test_the_observer_hears_each_take_and_the_release_that_frees_the_stage():
    reservation = StageMotionReservation()
    seen = _observed(reservation)

    hold = reservation.take("an acquisition is running")
    assert seen == ["an acquisition is running"]  # before take() returns: before the owner moves anything
    hold.release()

    assert seen == ["an acquisition is running", None]


def test_a_refused_take_and_a_stale_release_tell_the_observer_nothing():
    reservation = StageMotionReservation()
    first = reservation.take("an acquisition is running")
    first.release()
    second = reservation.take("a move to the loading position is running")
    seen = _observed(reservation)

    with pytest.raises(StageMotionRefused):
        reservation.take("a stage move from the control server is running")
    first.release()  # stale: the stage is the second owner's
    first.release()

    assert seen == [] and reservation.holder == "a move to the loading position is running"
    second.release()
    second.release()  # a second release of the current hold: already free
    assert seen == [None]


def test_the_observer_runs_outside_the_reservation_lock():
    """It sends a controller command and waits for it: the holder can be read, and a take refused, meanwhile."""
    reservation = StageMotionReservation()
    seen = []

    def observer(holder):
        seen.append((holder, reservation.holder))
        if holder is not None:
            with pytest.raises(StageMotionRefused):
                reservation.take("a stage move from the control server is running")

    reservation.on_holder_changed = observer
    reservation.take("an acquisition is running").release()

    assert seen == [("an acquisition is running", "an acquisition is running"), (None, None)]


def test_an_observer_that_raises_leaves_the_stage_with_its_owner_and_frees_it_on_release():
    reservation = StageMotionReservation()

    def broken(holder):
        raise RuntimeError("the controller refused the command")

    reservation.on_holder_changed = broken

    hold = reservation.take("an acquisition is running")  # the take still succeeds: the owner can release it
    assert reservation.holder == "an acquisition is running"
    hold.release()

    assert reservation.holder is None
    reservation.take("a move to the loading position is running")


def test_the_last_call_the_observer_gets_says_who_holds_the_stage_now():
    """A release and the next take on other threads: their calls are serialized, each with the holder as it is when it
    runs, so a release's None cannot land after the next owner's lock."""
    reservation = StageMotionReservation()
    seen = _observed(reservation)
    owners, cycles = 4, 200
    start = threading.Barrier(owners)

    def churn(i):
        start.wait()
        for _ in range(cycles):
            try:
                hold = reservation.take(f"owner {i}")
            except StageMotionRefused:
                continue
            hold.release()

    threads = [threading.Thread(target=churn, args=(i,)) for i in range(owners)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert reservation.holder is None
    assert seen and seen[-1] is None
