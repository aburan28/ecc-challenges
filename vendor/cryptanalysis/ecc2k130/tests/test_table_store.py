import copy
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('table_store', ROOT / 'table_store.py')
store = importlib.util.module_from_spec(spec)
spec.loader.exec_module(store)


class TableCatalogTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / 'tables/catalog.json').read_text())

    def testSelectionUsesActualFreeMemoryAndReserve(self):
        self.assertEqual(store.select(self.catalog, 24_000_000_000)['artifact']['branches'], 128)
        self.assertEqual(store.select(self.catalog, 88_000_000_000)['artifact']['branches'], 256)
        self.assertEqual(store.select(self.catalog, 88_000_000_000, 10 << 30)['artifact']['branches'], 128)
        self.assertIsNone(store.select(self.catalog, 16_000_000_000))
        for free, reserve in ((-1, 0), (0, -1), (True, 0)):
            with self.assertRaises(ValueError): store.select(self.catalog, free, reserve)

    def testCatalogRejectsWrongGeometryAndPaths(self):
        store.validate_catalog(self.catalog)
        for field, value in (('entries', 1), ('runtimeStatus', 'production-ready')):
            changed = copy.deepcopy(self.catalog); changed['artifacts'][0][field] = value
            with self.assertRaises(ValueError): store.validate_catalog(changed)
        for key in ('../credentials', '/absolute', 'https://example.com/pairs.bin'):
            changed = copy.deepcopy(self.catalog); changed['artifacts'][0]['files'][0]['key'] = key
            with self.assertRaises(ValueError): store.validate_catalog(changed)
        changed = copy.deepcopy(self.catalog); changed['domain']['knownScalar'] = 17
        with self.assertRaises(ValueError): store.validate_catalog(changed)

    def testGpuQueryDoesNotPoolDevices(self):
        result = subprocess.CompletedProcess([], 0, 'GPU-abc, 98304, 80000\n', '')
        with patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': 'GPU-abc, GPU-def'}), patch.object(store.subprocess, 'run', return_value=result) as run:
            info = store.gpu_memory(backend='cuda')
            self.assertEqual(info['freeBytes'], 80000 * (1 << 20))
            self.assertEqual(run.call_args.args[0][2], 'GPU-abc')
        with patch.dict(os.environ, {'CUDA_VISIBLE_DEVICES': ''}):
            with self.assertRaises(ValueError): store.gpu_memory(backend='cuda')

    def testMetalSelectionUsesAPlanningBudget(self):
        hardware = {'backend': 'metal', 'planningBudgetBytes': 40200896512,
                    'maxBufferBytes': 30150672384}
        plan = store.select_for_hardware(self.catalog, hardware)
        self.assertEqual(plan['artifact']['branches'], 128)
        self.assertEqual(plan['metalBuffersRequired'], 1)
        self.assertNotIn('freeGpuBytes', plan)
        self.assertFalse(plan['requiresSegmentedMetalLoader'])
        larger = {'backend': 'metal', 'planningBudgetBytes': 100_000_000_000,
                  'maxBufferBytes': 16 << 30}
        plan = store.select_for_hardware(self.catalog, larger)
        self.assertEqual(plan['artifact']['branches'], 256)
        self.assertEqual(plan['metalBuffersRequired'], 5)
        self.assertTrue(plan['requiresSegmentedMetalLoader'])

    def testBackendDispatch(self):
        with patch.object(store.sys, 'platform', 'darwin'), patch.object(store, 'metal_memory', return_value={'backend': 'metal'}) as metal:
            self.assertEqual(store.gpu_memory()['backend'], 'metal')
            metal.assert_called_once_with(None)
        with patch.object(store.sys, 'platform', 'linux'), patch.object(store, 'cuda_memory', return_value={'backend': 'cuda'}) as cuda:
            self.assertEqual(store.gpu_memory()['backend'], 'cuda')
            cuda.assert_called_once_with(None)

    def testMetalUnavailableFailsClearly(self):
        unavailable = subprocess.CompletedProcess([], 1, json.dumps({'status': 'unavailable', 'error': 'Metal GPU hidden by sandbox'}), '')
        with patch.object(store.Path, 'is_file', return_value=True), patch.object(store.subprocess, 'run', return_value=unavailable):
            with self.assertRaisesRegex(ValueError, 'hidden by sandbox'):
                store.metal_memory()


class DownloadTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dest = Path(self.temp.name) / 'pairs.bin'
        self.data = bytes(range(256)) * 1024
        self.item = {'bytes': len(self.data), 'sha256': hashlib.sha256(self.data).hexdigest()}
        self.mode, self.ranges = 'normal', []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                start = 0
                requestRange = self.headers.get('Range')
                owner.ranges.append(requestRange)
                if requestRange and owner.mode != 'ignore-range':
                    start = int(requestRange.split('=')[1].split('-')[0])
                    self.send_response(206)
                    self.send_header('Content-Range', 'bytes %d-%d/%d' % (start, len(owner.data)-1, len(owner.data)))
                else:
                    self.send_response(200)
                body = owner.data[start:]
                if owner.mode == 'corrupt': body = bytes([body[0] ^ 1]) + body[1:]
                self.send_header('Content-Length', len(body)); self.end_headers()
                try:
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True); self.thread.start()
        self.url = 'http://127.0.0.1:%d/pairs.bin' % self.server.server_port

    def tearDown(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(); self.temp.cleanup()

    def testDownloadAndVerifiedCacheReuse(self):
        store.download_file(self.item, self.url, self.dest)
        self.assertEqual(self.dest.read_bytes(), self.data)
        store.download_file(self.item, self.url, self.dest)
        self.assertEqual(len(self.ranges), 1)

    def testResumeExactRange(self):
        self.dest.with_name('pairs.bin.partial').write_bytes(self.data[:17013])
        store.download_file(self.item, self.url, self.dest)
        self.assertEqual(self.ranges, ['bytes=17013-'])
        self.assertEqual(self.dest.read_bytes(), self.data)

    def testIgnoredRangeCannotCorruptPartial(self):
        self.mode = 'ignore-range'
        partial = self.dest.with_name('pairs.bin.partial'); partial.write_bytes(self.data[:17013])
        with self.assertRaisesRegex(ValueError, 'resume range'):
            store.download_file(self.item, self.url, self.dest)
        self.assertEqual(partial.read_bytes(), self.data[:17013]); self.assertFalse(self.dest.exists())

    def testCorruptionIsNotPromoted(self):
        self.mode = 'corrupt'
        with self.assertRaisesRegex(ValueError, 'SHA-256'):
            store.download_file(self.item, self.url, self.dest)
        self.assertFalse(self.dest.exists())

    def testCacheTamperingAndLowDiskFail(self):
        self.dest.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError, 'cached file'):
            store.download_file(self.item, self.url, self.dest)
        self.dest.unlink()
        with patch.object(store.shutil, 'disk_usage', return_value=type('Usage', (), {'free': 1})()):
            with self.assertRaisesRegex(ValueError, 'disk space'):
                store.download_file(self.item, self.url, self.dest)
        self.assertEqual(self.ranges, [])


class PairReaderTests(unittest.TestCase):
    def testReadPastSigned32BitIndex(self):
        h, n, entries = 256, 67072, 2249360128
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'pairs.bin'
            words = (1, 2, 3, 4, 5, 6, 7, 8, 63)
            with path.open('wb') as out:
                out.truncate(entries * 36)  # Sparse unit-test fixture, removed on exit.
                out.seek((entries - 1) * 36); out.write(struct.pack('<9I', *words))
                out.seek(0); out.write(struct.pack('<9I', *((0,) * 8 + (0x80000000,))))
            receipt = {'schema': 'ecc2k-table-cache-v1', 'branches': h, 'signedDirections': n,
                       'entries': entries, 'files': {'pairs.bin': str(path)}}
            reader = store.PairTable(receipt)
            try:
                self.assertIsNone(reader.lookup(0, 0))
                x, y = reader.lookup(n - 1, n - 1)
                self.assertEqual(x >> 128, 7); self.assertEqual(y >> 128, 7)
                self.assertEqual(reader.direction(255, 130, 1), n - 1)
                with self.assertRaises(ValueError): reader.lookup(n, 0)
            finally: reader.close()


if __name__ == '__main__': unittest.main()
