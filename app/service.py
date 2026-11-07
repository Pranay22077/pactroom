"""Long-lived Relay worker.

Establish an authenticated Switch event subscription before dispatching work:
otherwise a fast reply can arrive before a fresh ``start_from=head`` stream
exists and be lost permanently.
"""
import json
from contextlib import contextmanager
from .switch_adapter import SwitchAdapter, EventRouter
from .notifications import Notifications


def rejection_reason(error):
    # Only fixed strings are logged. Never include a message body or arbitrary
    # exception text, which could contain private project data.
    known = {
        'Commit all intended files before review; working tree is not clean': 'dirty_candidate',
        'Candidate commit is no longer HEAD': 'candidate_head_changed',
        'Stale candidate': 'stale_candidate',
        'Stale review brief': 'stale_review_brief',
        'Invalid review verdict': 'invalid_review_verdict',
        'Unrecognized result sender': 'unrecognized_sender',
        'Cross-room or wrong-role completion rejected': 'wrong_room_or_role',
    }
    if isinstance(error, json.JSONDecodeError): return 'invalid_json'
    return known.get(str(error), 'invalid_result_or_envelope')


@contextmanager
def worker_lock(path):
    """Linux service lock prevents two SSE readers/dispatchers sharing one identity."""
    import fcntl
    with open(path, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def serve(runner, config, state, until_task=None):
    if config.get('live_enabled') is not True:
        raise ValueError('Live mode is disabled until identity mappings and adapter are qualified')

    adapter = SwitchAdapter(config)
    router = EventRouter(runner, config)
    notices = Notifications(runner, adapter, config)
    def finished():
        return until_task is not None and runner.task(until_task)['state'] in ('READY_FOR_APPROVAL','BLOCKED')
    with worker_lock(state / 'runner.lock'):
        if finished(): return
        runner.recover()
        runner.schedule()
        saved = runner.ledger.rows("SELECT value FROM metadata WHERE key='switch_position'")
        position = json.loads(saved[0]['value']) if saved else None

        def save_position(frame):
            nonlocal position
            update = frame.get('position')
            if update is None or update == {}:
                return
            if not isinstance(update, dict):
                raise ValueError('Switch event position must be an object')
            position = {**(position or {}), **update}
            with runner.ledger.transaction() as db:
                db.execute(
                    "INSERT INTO metadata VALUES('switch_position',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (json.dumps(position),),
                )

        # frames() is a generator: obtaining it does NOT open the SSE connection.
        # Advancing to the verified handshake does. No agent work may be sent
        # before this point, including on a fresh ledger with no saved cursor.
        stream = adapter.frames(position)
        try:
            try:
                kind, data = next(stream)
            except StopIteration:
                raise ConnectionError('Switch event stream ended before handshake') from None
            if kind != 'connection_state' or data.get('agent_id') != config['runner_agent_id']:
                raise ValueError('Expected authenticated Runner connection_state before dispatch')
            save_position(data)
            notices.flush()

            # A very fast model response is now buffered in the established SSE
            # connection and will be read by the loop below.
            runner.dispatch_one(adapter)

            for kind, data in stream:
                runner.expire()
                if kind == 'message':
                    try:
                        router.consume(data)
                    except (ValueError, KeyError, TypeError) as error:
                        # Bad/untrusted message content must not kill the worker or
                        # become an endlessly replayed poison event. No body is logged.
                        with runner.ledger.transaction() as db:
                            runner.ledger.event(db, None, 'message_rejected', {
                                'message_id': data.get('payload', {}).get('message_id'),
                                'room_id': data.get('room_id'),
                                'error_type': type(error).__name__,
                                'reason': rejection_reason(error),
                            }, runner.clock())
                save_position(data)
                notices.flush()
                if finished(): return
                # Dispatch only after processing and checkpointing each frame.
                runner.dispatch_one(adapter)
        finally:
            stream.close()
