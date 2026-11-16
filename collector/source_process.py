"""Contain browser/parser hangs so a single source cannot starve the queue."""
import multiprocessing
import os
import signal
import threading
from datetime import datetime, timedelta, timezone
from .client import Client, DatabaseError


def _child(source, selected, preview, run_id, connection):
    if os.name == 'posix':
        os.setsid()
    from .sync import sync_one, expected_source_error
    try:
        result = sync_one(Client(), source, selected, preview, run_id=run_id)
        connection.send({'counts': result})
    except Exception as exc:
        connection.send({'error': str(exc)[:750], 'source_error': expected_source_error(exc)})
    finally:
        connection.close()


def _stop(process):
    if process.is_alive():
        # The child owns its process group, including Playwright/Chromium.
        try:
            if os.name == 'posix' and os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
        process.join(5)


def finish_interrupted(db, source, run_id, message):
    now = datetime.now(timezone.utc)
    db.update_run(run_id, {'status': 'failed', 'completed_at': now.isoformat(),
                          'error_message': '[worker_interrupted] ' + message})
    db.request('PATCH', 'conference_source_parse_results',
        params={'run_id': 'eq.' + run_id, 'status': 'eq.running'},
        json={'status': 'failed', 'completed_at': now.isoformat(), 'heartbeat_at': now.isoformat(),
              'progress_stage': 'complete', 'errors': [{'message': message}]})
    db.update_source(source['id'], {'last_attempt_at': now.isoformat(), 'last_error': message,
        'next_check_at': (now + timedelta(hours=6)).isoformat()})


def run_source(db, source, selected=None, preview=False, timeout=None):
    seconds = max(1, min(float(timeout or os.environ.get('MAX_SOURCE_SECONDS', '300')), 600))
    run_id = db.create_run(source['id'])
    context = multiprocessing.get_context('spawn')
    reader, writer = context.Pipe(duplex=False)
    process = context.Process(target=_child, args=(source, selected, preview, run_id, writer))
    started = False
    old_term = None
    if threading.current_thread() is threading.main_thread():
        def interrupted(*_):
            raise KeyboardInterrupt('Worker received a stop signal')
        old_term = signal.signal(signal.SIGTERM, interrupted)
    try:
        process.start()
        started = True
        writer.close()
        # Read the pipe before joining so a full pipe cannot deadlock the child.
        if not reader.poll(seconds):
            _stop(process)
            message = f'Source exceeded its {seconds:g}-second execution limit; remaining sources can continue.'
            finish_interrupted(db, source, run_id, message)
            raise TimeoutError(message)
        try:
            result = reader.recv()
        except EOFError:
            message = 'Source process stopped without a completion report.'
            finish_interrupted(db, source, run_id, message)
            raise DatabaseError(message) from None
        process.join(5)
        _stop(process)
        if 'error' in result:
            if result['source_error']:
                raise ValueError(result['error'])
            raise DatabaseError(result['error'])
        return result['counts']
    except BaseException as exc:
        if started:
            _stop(process)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)) or not started:
            finish_interrupted(db, source, run_id, 'Worker stopped before this source completed.')
        raise
    finally:
        if old_term is not None:
            signal.signal(signal.SIGTERM, old_term)
        reader.close()
        writer.close()
        if started and not process.is_alive():
            process.close()


def close_current_workflow():
    repository, run = os.environ.get('GITHUB_REPOSITORY'), os.environ.get('GITHUB_RUN_ID')
    if not repository or not run or not run.isdigit():
        return
    db = Client()
    rows = db.request('GET', 'conference_ingestion_runs', params={
        'select': 'id,source_id', 'status': 'eq.running', 'limit': '20',
        'worker_run_url': f'eq.https://github.com/{repository}/actions/runs/{run}'})
    for row in rows:
        finish_interrupted(db, {'id': row['source_id']}, row['id'],
                           'GitHub execution ended without completing this source.')


if __name__ == '__main__':
    close_current_workflow()

