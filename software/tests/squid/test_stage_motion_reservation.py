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
