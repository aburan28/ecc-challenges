import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import publish_tables as publish


class PublicationTests(unittest.TestCase):
    def testPublicMetadataExcludesLocalPathsAndCommands(self):
        with tempfile.TemporaryDirectory() as temp:
            catalog, uploads, _ = publish.prepare(Path(temp))
            text = json.dumps(catalog)
            self.assertNotIn('/Users/', text); self.assertNotIn('/Volumes/', text)
            self.assertEqual(len(uploads), 8)
            for item in uploads:
                self.assertIn(item['name'], ('pairs.bin', 'directions.bin', 'coefficients.json', 'table.json'))
                if item['name'] == 'table.json':
                    meta = json.loads(Path(item['source']).read_text())
                    self.assertNotIn('command', meta); self.assertNotIn('compileCommand', meta)
                    self.assertNotIn('platform', meta)

    def testCatalogIsPublishedLast(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            _, uploads, catalogPath = publish.prepare(root)
            items = {item['key']: item for item in uploads}
            uploaded, calls = set(), []
            realDigest = publish.table_store.digest
            def checksum(path):
                if not Path(path).exists():
                    return next(item['sha256'] for item in uploads if item['source'] == str(path))
                return realDigest(path)
            def aws(args, profile=None, region=None, allow_missing=False):
                calls.append(args)
                if args[:2] == ['sts', 'get-caller-identity']: return {'Account': 'test-account'}
                if args[:2] == ['s3api', 'head-object']:
                    key = args[args.index('--key') + 1].removeprefix('public/test/')
                    if key not in uploaded: return None
                    item = items[key]
                    return {'ContentLength': item['bytes'], 'Metadata': {'sha256': item['sha256']}}
                if args[:2] == ['s3', 'cp']:
                    key = args[3].removeprefix('s3://test-bucket/public/test/')
                    uploaded.add(key); return {}
                raise AssertionError(args)
            with patch.object(publish, 'aws', side_effect=aws), patch.object(publish, 'public_check') as check, \
                    patch.object(publish.table_store, 'digest', side_effect=checksum), \
                    patch.object(publish, 'urlopen', side_effect=lambda *a, **kw: io.BytesIO(catalogPath.read_bytes())):
                result = publish.publish(root, 'test-bucket', 'us-west-2', 'public/test')
            self.assertTrue(result['anonymousCatalogVerified'])
            self.assertEqual(check.call_count, 8)
            self.assertTrue(calls[-1][3].endswith('/catalog.json'))
            self.assertEqual(len(uploaded), 9)

    def testPublicRetryIsBounded(self):
        error = HTTPError('https://example.com/file', 403, 'Forbidden', {}, io.BytesIO())
        with patch.object(publish, 'public_check_once', side_effect=[error, None]) as call, patch.object(publish.time, 'sleep') as sleep:
            publish.public_check('https://example.com/file', Path('unused'), 10)
            self.assertEqual(call.call_count, 2); sleep.assert_called_once_with(1)
        with patch.object(publish, 'public_check_once', side_effect=ValueError('wrong content')), patch.object(publish.time, 'sleep') as sleep:
            with self.assertRaises(ValueError): publish.public_check('https://example.com/file', Path('unused'), 10)
            sleep.assert_not_called()


if __name__ == '__main__': unittest.main()
