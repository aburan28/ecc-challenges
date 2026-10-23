"""Exporter observations stay finite, source-bound, and distinct from missing data."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

EXPORTER = Path(os.environ.get('PM_TEST_EXPORTER') or
                Path(__file__).resolve().parents[1] / 'tools/progress_metrics.py')
spec = importlib.util.spec_from_file_location('progress_metrics', EXPORTER)
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def samples(call):
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        call()
    return [json.loads(line) for line in output.getvalue().splitlines()]


class ProgressMetricsTests(unittest.TestCase):
    def test_nonfinite_values_do_not_become_samples(self):
        def export():
            for value in [float('nan'), float('inf'), -float('inf'), 'Infinity', 10 ** 400, None]:
                metrics.emit('invalid', value)
            metrics.emit('zero', 0)
        self.assertEqual([(x['metric'], x['value']) for x in samples(export)], [('zero', 0)])

    def test_missing_rates_are_absent_real_zero_and_observation_sources_survive(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(metrics, 'INGEST_DIR', Path(tmp)):
            records = [
                {'generated_at': '2026-10-01T00:00:00Z', 'dps': 1, 'work': {'iterations_log2': 32}},
                {'generated_at': '2026-10-02T00:00:00Z', 'walk_rate': {'iterations_per_second': 0}},
                {'generated_at': '2026-10-03T00:00:00Z', 'walk_rate': {'iterations_per_second': 8}},
                {'generated_at': '2026-10-03T01:00:00Z', 'walk_rate': {'iterations_per_second': 2}},
                {'generated_at': '2026-10-04T00:00:00Z', 'walk_rate': {'iterations_per_second': None}},
                {'generated_at': '2026-10-05T00:00:00Z', 'walk_rate': {'iterations_per_second': 'Infinity'}},
            ]
            for i, record in enumerate(records):
                (Path(tmp) / f'status-{i}.json').write_text(json.dumps(record))
            rows = samples(lambda: metrics.ingest_history('2026-10-01'))
        self.assertEqual([r['value'] for r in rows if r['metric'].endswith('iterations_log2')], [32])
        rates = [r for r in rows if r['metric'].endswith('walk_rate_ips_peak')]
        self.assertEqual({r['date']: r['value'] for r in rates}, {'2026-10-02': 0, '2026-10-03': 8})
        peak = next(r for r in rates if r['value'] == 8)
        self.assertTrue(peak['source'].startswith('status-2.json sha256:'), peak)

    def test_gateway_preserves_counts_and_omits_estimates(self):
        data = {'total_archived_points': 5, 'total_estimated_work_log2': 30,
                'total_estimated_work_log2_including_historical': 31,
                'campaigns': [{'id': 'ecc2k_130', 'received_points': 100,
                               'operations_per_second_log2': 20, 'expected_work_percent': 1,
                               'hours_to_expected_work': 99, 'points_per_hour': 0}]}
        with mock.patch.object(metrics, '_fetch_json', return_value=data):
            rows = samples(metrics.gateway)
        self.assertEqual({r['metric']: r['value'] for r in rows}, {
            'pool.total.archived_points': 5, 'pool.ecc2k-130.received_points': 100,
            'pool.ecc2k-130.points_per_hour': 0})

    def test_request_timeout_returns_without_retries_or_samples(self):
        with mock.patch.object(metrics.subprocess, 'run', side_effect=subprocess.TimeoutExpired('fetch', 25)) as run:
            with contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(samples(metrics.gateway), [])
        self.assertEqual(run.call_count, 1)
        self.assertLessEqual(run.call_args.kwargs['timeout'], 25)

    def test_json_fetch_uses_stdlib_and_handles_invalid_shape(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'status.json'
            path.write_text('{"total_archived_points": 7}')
            self.assertEqual(metrics._fetch_json(path.as_uri()), {'total_archived_points': 7})
            path.write_text('[]')
            with self.assertRaises(ValueError):
                metrics._fetch_json(path.as_uri())

    def test_committed_problems_and_invalid_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            def git(*args):
                return subprocess.run(['git', '-C', tmp, *args], check=True, capture_output=True, text=True)
            git('init', '-q', '-b', 'main')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.invalid')
            problem = Path(tmp) / 'src/ecc_challenges/multicurve/instances.json'
            problem.parent.mkdir(parents=True)
            problem.write_text(json.dumps({'instances': [{'id': 'recorded'}]}))
            git('add', '.')
            git('commit', '-qm', 'Recorded fixture')
            git('update-ref', 'refs/remotes/origin/main', 'HEAD')
            problem.write_text(json.dumps({'instances': [{'id': 'uncommitted'}, {'id': 'ignored'}]}))
            with mock.patch.object(metrics, 'REPO', Path(tmp)), mock.patch.dict(os.environ, {}, clear=True):
                with mock.patch.object(sys, 'argv', ['progress_metrics.py']):
                    args = metrics.args()
                rows = samples(lambda: metrics.problems(args.ref))
                self.assertEqual(rows[0]['value'], 1)
                self.assertEqual(rows[0]['source'], git('rev-parse', 'HEAD').stdout.strip() + ':src/ecc_challenges/multicurve/instances.json')
                with mock.patch.object(sys, 'argv', ['progress_metrics.py', '--ref', 'missing']):
                    with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                        metrics.args()


if __name__ == '__main__':
    unittest.main()
