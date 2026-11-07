# Workshop registration

A standard-library-only library for sequential, in-memory workshop registration,
cancellation, and FIFO promotion, with exactly two confirmed places.

```python
from datetime import datetime, timedelta, timezone
from workshop_registration import WorkshopRegistration

start = datetime(2030, 1, 1, 15, tzinfo=timezone.utc)
now = start - timedelta(minutes=120)
workshop = WorkshopRegistration(start, clock=lambda: now)
workshop.register("A")  # 'confirmed'
workshop.register("B")  # 'confirmed'
workshop.register("C")  # 'waitlisted'
workshop.cancel("A")    # None; exactly 120 minutes: reserve for staff
workshop.pending_places # 1
workshop.promote()      # 'C'; explicit staff promotion
workshop.confirmed      # ('B', 'C')
workshop.waitlist       # ()
```

`WorkshopRegistration(start_time, clock=None)` requires a UTC-aware datetime
with zero UTC offset. The default clock is `datetime.now(timezone.utc)`.
An injected callable clock must return a zero-offset aware datetime too. Each
mutation samples it exactly once; reads and construction do not sample it.
Naive, non-datetime, and nonzero-offset times raise `ValueError`. Calls are
sequential and the clock is assumed nondecreasing; there is no background work.

IDs must be nonblank strings, unique across both collections. They are preserved
exactly and compared case-sensitively: `A`, `a`, and ` A ` are distinct.
Invalid IDs, duplicates, unknown cancellations, and impossible promotions raise
`ValueError` without changing participants, order, or pending places. A canceled
ID may register again as a new arrival.

Before start:

- Registration confirms an ID only if there is an unreserved free place and no
  waitlist; otherwise it joins the FIFO waitlist.
- Waiting cancellation removes only that ID and preserves the remaining order.
- Confirmed cancellation strictly more than 120 minutes before start automatically
  confirms the oldest waiter, or leaves an ordinary free place if none is waiting.
- At exactly 120 minutes and closer, confirmed cancellation reserves a pending
  staff place, even with an empty waitlist. Registrations cannot fill that place
  directly. Waiting cancellations and elapsed time do not release it.
- `promote()` fills one free place with the oldest waiter and returns that ID.
  It consumes a pending place first if present. Full confirmation or an empty
  waitlist makes promotion invalid. Staff promotion is a library operation;
  there is no authentication system.

At or after start, registration, cancellation, and promotion raise `ValueError`
without mutation. Read-only `confirmed` and `waitlist` properties return immutable
tuple snapshots; `pending_places` returns the reserved-place count. Instances are
independent and reads remain available after start. Construction at or after
start is allowed but mutations are rejected.

Capacity changes, persistence, concurrency guarantees, UI, and network services
are outside scope. Run deterministic tests without sleeps or dependencies:

```sh
python3 -m unittest discover -s tests -v
```
