import multiprocessing
import time
import unittest
from unittest.mock import Mock, patch
from collector.source_process import run_source


def hang(*args):
    while True:
        pass


def succeed(source, selected, preview, run_id, pipe):
    pipe.send({'counts': {'fetched_count': 1}})
    pipe.close()


class SourceProcessTests(unittest.TestCase):
    def execute(self, target, timeout):
        actual = multiprocessing.get_context('spawn')
        context = Mock()
        context.Pipe.side_effect = actual.Pipe
        processes = []
        def spawn(**kwargs):
            process = actual.Process(target=target, args=kwargs['args'])
            processes.append(process)
            return process
        context.Process.side_effect = spawn
        db = Mock()
        db.create_run.return_value = 'run-1'
        def call():
            with patch('collector.source_process.multiprocessing.get_context', return_value=context):
                return run_source(db, {'id': 'source-1'}, timeout=timeout)
        return db, processes, call

    def test_cpu_hang_is_killed_and_run_and_source_are_finalized(self):
        db, processes, call = self.execute(hang, 1)
        started = time.monotonic()
        with self.assertRaises(TimeoutError):
            call()
        self.assertLess(time.monotonic() - started, 8)
        self.assertIn('[worker_interrupted]', db.update_run.call_args.args[1]['error_message'])
        self.assertEqual(db.update_run.call_args.args[1]['status'], 'failed')
        db.request.assert_called_once()
        self.assertIn('next_check_at', db.update_source.call_args.args[1])

    def test_healthy_source_returns_without_interruption(self):
        db, processes, call = self.execute(succeed, 5)
        self.assertEqual(call(), {'fetched_count': 1})
        db.update_run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
