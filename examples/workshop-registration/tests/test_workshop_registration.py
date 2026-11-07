import unittest
from datetime import datetime, timedelta, timezone

from workshop_registration import WorkshopRegistration


START = datetime(2030, 1, 1, 15, tzinfo=timezone.utc)


def make_workshop():
    return WorkshopRegistration(START, lambda: START - timedelta(hours=3))


class TestWorkshopRegistration(unittest.TestCase):
    def test_capacity_boundary_and_fifo(self):
        workshop = make_workshop()
        self.assertEqual(workshop.confirmed, ())
        self.assertEqual(workshop.waitlist, ())
        for participant, placement in zip(
            "ABCD", ("confirmed", "confirmed", "waitlisted", "waitlisted")
        ):
            self.assertEqual(workshop.register(participant), placement)
        self.assertEqual(workshop.confirmed, ("A", "B"))
        self.assertEqual(workshop.waitlist, ("C", "D"))

    def test_invalid_inputs_are_atomic_at_every_occupancy(self):
        for occupancy in range(4):
            for invalid in ("", " ", "\t\n", None, 0, 2.5, False, [], {}):
                with self.subTest(occupancy=occupancy, invalid=invalid):
                    workshop = make_workshop()
                    for participant in "ABC"[:occupancy]:
                        workshop.register(participant)
                    before = workshop.confirmed, workshop.waitlist, workshop.pending_places
                    with self.assertRaises(ValueError):
                        workshop.register(invalid)
                    self.assertEqual((workshop.confirmed, workshop.waitlist, workshop.pending_places), before)
                    expected = "confirmed" if occupancy < 2 else "waitlisted"
                    self.assertEqual(workshop.register("D"), expected)
                    successful = "ABC"[:occupancy] + "D"
                    self.assertEqual(workshop.confirmed, tuple(successful[:2]))
                    self.assertEqual(workshop.waitlist, tuple(successful[2:]))

    def test_duplicates_in_both_states_are_atomic(self):
        for occupancy in range(1, 4):
            for duplicate in "ABC"[:occupancy]:
                with self.subTest(occupancy=occupancy, duplicate=duplicate):
                    workshop = make_workshop()
                    for participant in "ABC"[:occupancy]:
                        workshop.register(participant)
                    before = workshop.confirmed, workshop.waitlist, workshop.pending_places
                    with self.assertRaises(ValueError):
                        workshop.register(duplicate)
                    self.assertEqual((workshop.confirmed, workshop.waitlist, workshop.pending_places), before)
                    workshop.register("D")
                    successful = "ABC"[:occupancy] + "D"
                    self.assertEqual(workshop.confirmed, tuple(successful[:2]))
                    self.assertEqual(workshop.waitlist, tuple(successful[2:]))

    def test_ids_are_preserved_and_case_sensitive(self):
        workshop = make_workshop()
        for participant in ("A", "a", " A ", "A "):
            workshop.register(participant)
        self.assertEqual(workshop.confirmed, ("A", "a"))
        self.assertEqual(workshop.waitlist, (" A ", "A "))

    def test_snapshots_are_immutable_and_detached(self):
        workshop = make_workshop()
        empty = workshop.confirmed, workshop.waitlist
        for participant in "ABC":
            workshop.register(participant)
        confirmed, waitlist = workshop.confirmed, workshop.waitlist
        with self.assertRaises(TypeError):
            confirmed[0] = "X"
        with self.assertRaises(TypeError):
            waitlist[0] = "X"
        for property_name in ("confirmed", "waitlist"):
            with self.assertRaises(AttributeError):
                setattr(workshop, property_name, ())
        workshop.register("D")
        self.assertEqual(empty, ((), ()))
        self.assertEqual(confirmed, ("A", "B"))
        self.assertEqual(waitlist, ("C",))
        self.assertEqual(workshop.waitlist, ("C", "D"))

    def test_instances_are_independent(self):
        first, second = make_workshop(), make_workshop()
        for participant in "ABC":
            first.register(participant)
        self.assertEqual((second.confirmed, second.waitlist), ((), ()))
        self.assertEqual(second.register("A"), "confirmed")
        self.assertEqual(first.confirmed, ("A", "B"))
        self.assertEqual(first.waitlist, ("C",))


if __name__ == "__main__":
    unittest.main()
