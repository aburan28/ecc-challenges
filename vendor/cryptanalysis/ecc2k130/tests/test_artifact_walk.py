import importlib.util
import hashlib
import json
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'metal'))
sys.path.insert(0, str(ROOT / 'research' / 'step_table'))
import artifact_reference as reference
import continuous_walk
import run_walk
import pack


def encode(point):
    if point is None:
        return struct.pack('<9I', *((0,) * 8 + (0x80000000,)))
    x, y = point
    words = [(x >> (32 * i)) & 0xffffffff for i in range(4)]
    words += [(y >> (32 * i)) & 0xffffffff for i in range(4)]
    words += [(x >> 128) | ((y >> 128) << 3)]
    return struct.pack('<9I', *words)


class ArtifactReferenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        record = json.loads((ROOT / 'research/step_table/table32-20260921.json').read_text())
        positive, _ = pack.pack(record, 8)
        directions = bytearray()
        for offset in range(0, len(positive), 36):
            point = reference.decode(positive[offset:offset + 36])
            directions.extend(encode(point))
            directions.extend(encode(cls.negate(point)))
        cls.walk = reference.Reference(bytes(directions), 8)

    @staticmethod
    def negate(point):
        return None if point is None else (point[0], point[0] ^ point[1])

    def testSelectorConstantsAndDirections(self):
        for value in (0, 1, 3, (1 << 131) - 1, 0x123456789abcdef):
            self.assertEqual(reference.table.popcount(value), bin(value).count('1'))
        self.assertEqual(len(self.walk.constants()), 13708)
        self.assertEqual(len(self.walk.points), 2 * 131 * 8)
        for direction in range(0, len(self.walk.points), 71):
            self.assertEqual(self.walk.points[direction ^ 1], self.negate(self.walk.points[direction]))

    def testReplayIsDeterministicAndCoefficientFree(self):
        state1, reports1 = self.walk.replay(3, 17, 20260921, 96, -1)
        state2, reports2 = self.walk.replay(3, 17, 20260921, 96, -1)
        self.assertEqual((state1, reports1), (state2, reports2))
        values = reference.STATE.unpack(state1)
        self.assertEqual(values[16], 95)  # one seed addition, then 95 walk updates
        self.assertEqual(values[17], 1)
        self.assertEqual(values[20], 0)
        self.assertEqual(reports1, [])

    def testRelaxedDistinguishedPointsReseedAndReport(self):
        state, reports = self.walk.replay(0, 8, 1234, 48, 64)
        values = reference.STATE.unpack(state)
        self.assertGreater(values[17], 1)
        self.assertEqual(values[20], len(reports))
        for raw in reports:
            report = reference.REPORT.unpack(raw)
            self.assertEqual(report[2], 0)
            self.assertEqual(report[-1], 0)

    def testExceptionalSeedExhaustionIsTerminal(self):
        state = self.walk.initial(reference.MASK - 3)
        state['mode'] = 1
        self.walk.cycle(state, 0, 8, 130)
        # First cycle seeds; the next reports and cannot allocate seed+lanes.
        self.walk.cycle(state, 0, 8, 130)
        self.assertEqual(state['mode'], 3)

    def testSeedStridePartitionsReseeds(self):
        state = self.walk.initial(1234)
        for _ in range(256):
            self.walk.cycle(state, 0, 4, 64, 12)
            if state['seed'] != 1234:
                break
        self.assertEqual(state['seed'], 1246)

    def testResidueShardsStayDisjointWithDifferentLaneCounts(self):
        streams = []
        for shard, lanes in ((0, 3), (1, 5)):
            args = SimpleNamespace(seed=1000, shard_index=shard,
                                   shard_count=2, lanes=lanes)
            stride = lanes * args.shard_count
            streams.append({continuous_walk.laneSeed(args, lane) + round_ * stride
                            for lane in range(lanes) for round_ in range(20)})
        self.assertFalse(streams[0] & streams[1])
        self.assertEqual({seed % 2 for seed in streams[0]}, {0})
        self.assertEqual({seed % 2 for seed in streams[1]}, {1})

    def testThroughputAccounting(self):
        report = {'gpuSeconds': 2.0, 'dispatchWallSeconds': 4.0,
                  'walkUpdates': 1_000, 'groupOperations': 1_200,
                  'iterationsPerSecond': 500.0, 'millionIterationsPerSecond': 0.0005,
                  'chargedGroupOperationsPerSecond': 600.0, 'wallIterationsPerSecond': 250.0}
        run_walk.validateRates(report)
        report['iterationsPerSecond'] = 501.0
        with self.assertRaisesRegex(ValueError, 'accounting mismatch'):
            run_walk.validateRates(report)

    def testReplayCanContinueFromSerializedState(self):
        first, reports1 = self.walk.replay(0, 1, 1234, 31, 64)
        resumed, reports2 = self.walk.replay_from(first, 0, 1, 47, 64)
        complete, reports = self.walk.replay(0, 1, 1234, 78, 64)
        self.assertEqual(resumed, complete)
        self.assertEqual(reports1 + reports2, reports)

    def testResumedChunkUsesDeltaCounters(self):
        before, _ = self.walk.replay(0, 1, 1234, 31, 64)
        after, reports = self.walk.replay_from(before, 0, 1, 47, 64)
        old = reference.STATE.unpack(before)
        new = reference.STATE.unpack(after)
        updates = new[16] - old[16]
        seeds = new[17] - old[17]
        native = {'gpuSeconds': 1.0, 'dispatchWallSeconds': 2.0,
                  'walkUpdates': updates, 'seedAdditions': seeds,
                  'groupOperations': updates + seeds, 'dpRecords': len(reports),
                  'droppedRecords': 0, 'haltedLanes': new[13] == 2,
                  'exhaustedLanes': new[13] == 3, 'lanes': 1, 'branches': 8,
                  'iterationsPerSecond': float(updates),
                  'millionIterationsPerSecond': updates / 1e6,
                  'chargedGroupOperationsPerSecond': float(updates + seeds),
                  'wallIterationsPerSecond': updates / 2.0}
        config = {'lanes': 1, 'branches': 8, 'cycles': 47, 'launches': 1,
                  'dpWeight': 64}
        _, totals, delta, checked = continuous_walk.validateChunk(
            before, after, b''.join(reports), native, config, self.walk, 1, 3)
        self.assertEqual(delta['walkUpdates'], updates)
        self.assertEqual(totals['dpRecords'] - old[20], len(reports))
        self.assertEqual(checked, 1)
        native['walkUpdates'] += old[16]
        native['groupOperations'] += old[16]
        native['iterationsPerSecond'] = float(native['walkUpdates'])
        native['millionIterationsPerSecond'] = native['walkUpdates'] / 1e6
        native['chargedGroupOperationsPerSecond'] = float(native['groupOperations'])
        native['wallIterationsPerSecond'] = native['walkUpdates'] / 2.0
        with self.assertRaisesRegex(ValueError, 'delta accounting'):
            continuous_walk.validateChunk(
                before, after, b''.join(reports), native, config, self.walk, 0, 3)


class ContinuousPublicationTests(unittest.TestCase):
    class RecordingStore(continuous_walk.LocalStore):
        def __init__(self, root, failLatest=False):
            super().__init__(root)
            self.operations = []
            self.failLatest = failLatest

        def create(self, source, key):
            self.operations.append(('create', key))
            return super().create(source, key)

        def put(self, source, key):
            self.operations.append(('put', key))
            if self.failLatest and key == 'latest.json':
                raise OSError('offline')
            return super().put(source, key)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name) / 'work'
        self.input = self.work / 'input'
        self.pending = self.work / 'pending'
        self.input.mkdir(parents=True)
        self.pending.mkdir()
        self.oldState = b'o' * reference.STATE.size
        self.newState = b'n' * reference.STATE.size
        self.reports = b'd' * reference.REPORT.size
        (self.input / 'initial.bin').write_bytes(self.oldState)
        (self.pending / 'state.bin').write_bytes(self.newState)
        (self.pending / 'reports.bin').write_bytes(self.reports)
        stateSha = hashlib.sha256(self.newState).hexdigest()
        reportSha = hashlib.sha256(self.reports).hexdigest()
        self.manifest = {
            'schema': continuous_walk.SCHEMA, 'runIdentity': 'run-hash',
            'walkIdentity': 'walk-hash', 'sequence': 7,
            'previousStateSha256': hashlib.sha256(self.oldState).hexdigest(),
            'state': {'key': 'checkpoints/state-000000000007-%s.bin' % stateSha,
                      'sha256': stateSha, 'bytes': len(self.newState)},
            'distinguishedPoints': {
                'key': 'dp/chunk-000000000007-%s-%012d.bin' %
                       (reportSha, len(self.reports)),
                'sha256': reportSha, 'bytes': len(self.reports),
                'records': 1, 'recordBytes': 80,
                'recordFormat': continuous_walk.RECORD_FORMAT},
            'work': {'startingTotals': {'walkUpdates': 99, 'seedAdditions': 1,
                                        'dpRecords': 0},
                     'delta': {'walkUpdates': 1, 'seedAdditions': 1,
                               'dpRecords': 1}},
            'cumulative': {'walkUpdates': 100, 'seedAdditions': 2,
                           'dpRecords': 1, 'haltedLanes': 0,
                           'exhaustedLanes': 0}}
        continuous_walk.atomicJson(self.pending / 'manifest.json', self.manifest)
        continuous_walk.atomicJson(self.pending / 'native.json', {
            'iterationsPerSecond': 12.0})
        (self.pending / 'manifest-key.txt').write_text(
            'manifests/chunk-000000000007-%s.json\n' %
            continuous_walk.objectDigest(self.manifest))
        self.campaign = {'runIdentity': 'run-hash', 'walkIdentity': 'walk-hash',
                         'run': {'lanes': 1}}

    def testPointsPrecedeStateAndLatestAdvancesLast(self):
        store = self.RecordingStore(str(Path(self.temp.name) / 'store'))
        nextSequence, latest = continuous_walk.publishPending(
            store, continuous_walk.NoRemoteLease(), self.pending, self.input,
            self.campaign, self.work, 'local://store')
        self.assertEqual(nextSequence, 8)
        self.assertEqual(latest['sequence'], 7)
        self.assertEqual((self.input / 'initial.bin').read_bytes(), self.newState)
        self.assertFalse(self.pending.exists())
        keys = [key for operation, key in store.operations]
        self.assertEqual(keys[-1], 'latest.json')
        self.assertLess(keys.index(self.manifest['distinguishedPoints']['key']),
                        keys.index(self.manifest['state']['key']))
        self.assertLess(keys.index(self.manifest['state']['key']),
                        keys.index('latest.json'))

    def testLatestFailureRetainsPendingAndOldLocalBoundary(self):
        store = self.RecordingStore(str(Path(self.temp.name) / 'store'),
                                    failLatest=True)
        with self.assertRaisesRegex(OSError, 'offline'):
            continuous_walk.publishPending(
                store, continuous_walk.NoRemoteLease(), self.pending, self.input,
                self.campaign, self.work, 'local://store')
        self.assertTrue(self.pending.exists())
        self.assertEqual((self.input / 'initial.bin').read_bytes(), self.oldState)
        self.assertTrue(store.exists(self.manifest['distinguishedPoints']['key']))
        self.assertTrue(store.exists(self.manifest['state']['key']))

    def testFailedInitialPreparationCannotPoisonInputDirectory(self):
        prepareWork = self.work / 'prepare-fixture'
        prepareWork.mkdir()
        args = SimpleNamespace(
            work=prepareWork, catalog=None, artifact_dir=None, branches=128,
            lanes=1, cycles=1, chunk_launches=1, dp_cap=1, batch=1,
            dp_weight=32, seed=1, shard_count=1, progress_every=1)
        catalog = {'artifacts': [{'branches': 128}]}

        def fail(root, entry, value, base, artifactDir, config):
            root.mkdir()
            (root / 'directions.bin').write_bytes(b'partial')
            raise OSError('download interrupted')

        with patch.object(continuous_walk.table_store, 'load_catalog',
                          return_value=(catalog, 'https://example.test/')), \
                patch.object(continuous_walk.run_walk, 'prepare', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'interrupted'):
                continuous_walk.prepareInput(args)
        self.assertFalse((prepareWork / 'input').exists())
        self.assertEqual(list(prepareWork.glob('.input-preparing-*')), [])


class ContinuousLeaseTests(unittest.TestCase):
    class Slots:
        def __init__(self, item=None):
            self.item = item

        def _get(self, slot):
            return (dict(self.item), 'etag') if self.item is not None else (None, None)

        def _put(self, slot, item, ifMatch=None, create=False):
            self.item = dict(item)
            return True

        def _modify(self, slot, owner, update):
            if self.item is None or self.item.get('owner') != owner:
                return False
            update(self.item)
            return True

    def testFixedLeaseIsCreatedAndReleased(self):
        slots = self.Slots()
        with patch.object(continuous_walk, 'S3Slots', return_value=slots):
            lease = continuous_walk.S3RunLease('bucket', 'prefix', 'owner')
        lease.acquire()
        self.assertEqual(slots.item['owner'], 'owner')
        self.assertEqual(slots.item['state'], 'active')
        lease.release()
        self.assertEqual(slots.item['state'], 'idle')
        self.assertEqual(slots.item['leaseUntil'], 0)

    def testActiveFixedLeaseRejectsSecondOwner(self):
        slots = self.Slots({'owner': 'first', 'leaseUntil': 1 << 62})
        with patch.object(continuous_walk, 'S3Slots', return_value=slots):
            lease = continuous_walk.S3RunLease('bucket', 'prefix', 'second')
        with self.assertRaisesRegex(RuntimeError, 'active owner'):
            lease.acquire()


if __name__ == '__main__':
    unittest.main()
