"""Deterministic acceptance checks for cancellation and time policy."""
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from workshop_registration import WorkshopRegistration

START = datetime(2030, 1, 1, 15, tzinfo=timezone.utc)
MICROSECOND = timedelta(microseconds=1)


class Clock:
    def __init__(self):
        self.now = START - timedelta(hours=3)
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.now


class TestCancellation(unittest.TestCase):
    def make(self, ids="ABCD"):
        clock = Clock()
        workshop = WorkshopRegistration(START, clock)
        for participant in ids:
            workshop.register(participant)
        return workshop, clock

    def state(self, workshop):
        return workshop.confirmed, workshop.waitlist, workshop.pending_places

    def reject(self, workshop, operation):
        before = self.state(workshop)
        with self.assertRaises(ValueError):
            operation()
        self.assertEqual(self.state(workshop), before)

    def test_cutoff_microsecond_boundaries(self):
        for remaining, automatic in (
            (timedelta(minutes=120) + MICROSECOND, True),
            (timedelta(minutes=120), False),
            (timedelta(minutes=120) - MICROSECOND, False),
            (timedelta(minutes=119), False),
        ):
            with self.subTest(remaining=remaining):
                workshop, clock = self.make()
                clock.now = START - remaining
                self.assertIsNone(workshop.cancel("A"))
                expected = (("B", "C"), ("D",), 0) if automatic else (("B",), ("C", "D"), 1)
                self.assertEqual(self.state(workshop), expected)
                if not automatic:
                    self.assertEqual(workshop.promote(), "C")
                    self.assertEqual(self.state(workshop), (("B", "C"), ("D",), 0))

    def test_waiting_cancellation_preserves_order_and_pending(self):
        for late in (False, True):
            with self.subTest(late=late):
                workshop, clock = self.make("ABCDE")
                if late:
                    clock.now = START - timedelta(minutes=120)
                    workshop.cancel("A")
                before_confirmed = workshop.confirmed
                before_pending = workshop.pending_places
                workshop.cancel("D")
                self.assertEqual(self.state(workshop), (before_confirmed, ("C", "E"), before_pending))
                self.reject(workshop, lambda: workshop.cancel("D"))
                self.reject(workshop, lambda: workshop.cancel("unknown"))
                if late:
                    self.assertEqual(workshop.promote(), "C")
                    self.assertEqual(self.state(workshop), (("B", "C"), ("E",), 0))

    def test_early_empty_place_and_reregistration(self):
        workshop, clock = self.make("AB")
        workshop.cancel("A")
        self.reject(workshop, lambda: workshop.cancel("A"))
        self.assertEqual(workshop.register("E"), "confirmed")
        self.assertEqual(workshop.register("A"), "waitlisted")
        self.assertEqual(self.state(workshop), (("B", "E"), ("A",), 0))

    def test_pending_places_queue_new_arrivals_and_promote_fifo(self):
        workshop, clock = self.make("ABCD")
        clock.now = START - timedelta(minutes=120)
        workshop.cancel("A")
        self.assertEqual(workshop.register("E"), "waitlisted")
        workshop.cancel("B")
        self.assertEqual(self.state(workshop), ((), ("C", "D", "E"), 2))
        self.assertEqual(workshop.promote(), "C")
        self.assertEqual(self.state(workshop), (("C",), ("D", "E"), 1))
        self.assertEqual(workshop.promote(), "D")
        self.assertEqual(self.state(workshop), (("C", "D"), ("E",), 0))
        self.reject(workshop, workshop.promote)

    def test_empty_waitlist_pending_place_persists(self):
        workshop, clock = self.make("AB")
        clock.now = START - timedelta(minutes=120)
        workshop.cancel("A")
        self.reject(workshop, workshop.promote)
        clock.now += timedelta(minutes=1)
        self.assertEqual(self.state(workshop), (("B",), (), 1))
        self.assertEqual(workshop.register("E"), "waitlisted")
        workshop.cancel("E")
        self.assertEqual(self.state(workshop), (("B",), (), 1))
        self.assertEqual(workshop.register("F"), "waitlisted")
        self.assertEqual(workshop.promote(), "F")
        self.assertEqual(self.state(workshop), (("B", "F"), (), 0))

    def test_unreserved_and_pending_places_remain_distinct(self):
        workshop, clock = self.make("AB")
        workshop.cancel("A")
        clock.now = START - timedelta(minutes=120)
        workshop.cancel("B")
        self.assertEqual(self.state(workshop), ((), (), 1))
        self.assertEqual(workshop.register("C"), "confirmed")
        self.assertEqual(workshop.register("D"), "waitlisted")
        self.assertEqual(workshop.promote(), "D")
        self.assertEqual(self.state(workshop), (("C", "D"), (), 0))

    def test_invalid_operations_and_ids_are_atomic(self):
        for ids in ("", "A", "AB", "ABC"):
            workshop, clock = self.make(ids)
            self.reject(workshop, workshop.promote)
            for invalid in ("", " \t\n", None, 4, [], {}):
                self.reject(workshop, lambda: workshop.cancel(invalid))
            self.reject(workshop, lambda: workshop.cancel("missing"))
        workshop, clock = self.make(("A", "a", " A "))
        workshop.cancel("a")
        self.assertEqual(self.state(workshop), (("A", " A "), (), 0))

    def test_start_boundaries_for_each_mutation(self):
        for offset in (-MICROSECOND, timedelta(0), MICROSECOND):
            for action in ("register", "cancel", "promote"):
                with self.subTest(offset=offset, action=action):
                    workshop, clock = self.make()
                    if action == "promote":
                        clock.now = START - timedelta(minutes=1)
                        workshop.cancel("A")
                    clock.now = START + offset
                    operation = {"register": lambda: workshop.register("E"),
                                 "cancel": lambda: workshop.cancel("A"),
                                 "promote": workshop.promote}[action]
                    before = self.state(workshop)
                    if offset >= timedelta(0):
                        self.reject(workshop, operation)
                    else:
                        operation()
                        self.assertNotEqual(self.state(workshop), before)

    def test_clock_once_per_attempt_and_never_on_reads_or_construction(self):
        clock = Clock()
        workshop = WorkshopRegistration(START, clock)
        self.assertEqual(clock.calls, 0)
        for operation in (lambda: workshop.register("A"), lambda: workshop.register("A"),
                          lambda: workshop.cancel("missing"), lambda: workshop.cancel("A"),
                          workshop.promote, lambda: workshop.register(None)):
            before = clock.calls
            try:
                operation()
            except ValueError:
                pass
            self.assertEqual(clock.calls, before + 1)
            self.state(workshop)
            self.assertEqual(clock.calls, before + 1)
        clock.now = START
        self.reject(workshop, lambda: workshop.register("B"))

    def test_invalid_times_and_clocks(self):
        invalid = (None, "2030-01-01", 4, datetime(2030, 1, 1),
                   START.astimezone(timezone(timedelta(hours=1))))
        for value in invalid:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    WorkshopRegistration(value)
                for action in ("register", "cancel", "promote"):
                    workshop, clock = self.make()
                    clock.now = START - timedelta(minutes=1)
                    workshop.cancel("A")
                    clock.now = value
                    operation = {"register": lambda: workshop.register("E"),
                                 "cancel": lambda: workshop.cancel("B"),
                                 "promote": workshop.promote}[action]
                    before_calls = clock.calls
                    self.reject(workshop, operation)
                    self.assertEqual(clock.calls, before_calls + 1)
        for clock in (False, 3, "clock"):
            with self.assertRaises(ValueError):
                WorkshopRegistration(START, clock)
        zero_offset = timezone(timedelta(0), "zero")
        workshop = WorkshopRegistration(START.replace(tzinfo=zero_offset), lambda: START - MICROSECOND)
        self.assertEqual(workshop.register("A"), "confirmed")

    def test_default_clock_and_construction_after_start(self):
        class FixedDatetime(datetime):
            @classmethod
            def now(cls, tz):
                self.assertEqual(tz, timezone.utc)
                return cls(2030, 1, 1, 15, tzinfo=timezone.utc)

        with patch("workshop_registration.datetime", FixedDatetime):
            workshop = WorkshopRegistration(FixedDatetime(2030, 1, 1, 15, tzinfo=timezone.utc))
            self.reject(workshop, lambda: workshop.register("A"))
        for now in (START, START + MICROSECOND):
            workshop = WorkshopRegistration(START, lambda: now)
            self.assertEqual(self.state(workshop), ((), (), 0))
            self.reject(workshop, lambda: workshop.register("A"))
            self.reject(workshop, lambda: workshop.cancel("A"))
            self.reject(workshop, workshop.promote)

    def test_snapshots_and_isolation_after_cancellation_and_promotion(self):
        workshop, clock = self.make()
        other, _ = self.make("")
        saved = self.state(workshop)
        clock.now = START - timedelta(minutes=1)
        workshop.cancel("A")
        workshop.promote()
        self.assertEqual(saved, (("A", "B"), ("C", "D"), 0))
        self.assertEqual(self.state(other), ((), (), 0))
        for name in ("confirmed", "waitlist", "pending_places"):
            with self.assertRaises(AttributeError):
                setattr(workshop, name, 9)
