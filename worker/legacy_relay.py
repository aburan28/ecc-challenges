#!/usr/bin/env python3
"""Relay a legacy worker's unchanged DP bytes; retain files across rotation.

The operator must first enroll the existing run ID on the destination. This
process never starts or stops a walk, changes a seed, or deletes source data.
An exclusive lock and a write-ahead pending file allow exact POST retries.
"""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import time
from urllib.request import Request, urlopen
from urllib.error import HTTPError

ROOT = Path(os.environ.get('ECC_RELAY_ROOT', '/work/ecc2k130/public-bridge'))
SOURCE = Path(os.environ.get('ECC_RELAY_SOURCE', '/work/ecc2k130/gpu0/dp.bin'))
API = 'https://ecc-challenges.com'
RUN = 249


def atomic(path, data):
    tmp = path.with_suffix(path.suffix + '.tmp')
    with tmp.open('wb') as f:
        os.fchmod(f.fileno(), 0o600)
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    fd = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def save(state):
    atomic(ROOT / 'relay-state.json', json.dumps(state, sort_keys=True).encode())


def request(key, method, path, data=None):
    req = Request(API + path, data=data, method=method, headers={
        'Authorization': 'Bearer ' + key, 'Content-Type': 'application/octet-stream',
        'X-Campaign-Id': 'ecc2k-130', 'User-Agent': 'ecc-legacy-preserving-relay/1'})
    try:
        with urlopen(req, timeout=120) as r:
            return json.load(r)
    except HTTPError as e:
        raise RuntimeError('gateway HTTP ' + str(e.code)) from None


def check_records(data):
    if not data or len(data) % 32:
        raise RuntimeError('incomplete record')
    for i in range(0, len(data), 32):
        if int.from_bytes(data[i:i+8], 'little') >> 48 != RUN:
            raise RuntimeError('source run changed; preserve and enroll before continuing')
        x = int.from_bytes(data[i+8:i+32], 'little')
        if x >> 131 or bin(x).count('1') > 32 or bin(x).count('1') % 2:
            raise RuntimeError('source contains an invalid DP32 encoding')


def main():
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = (ROOT / 'relay.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    key = (ROOT / 'api-key').read_text().strip()
    state_path = ROOT / 'relay-state.json'
    if state_path.exists():
        state = json.loads(state_path.read_text())
    else:
        remote = request(key, 'GET', '/v1/runs/249')
        if remote['cursor'] != 0:
            raise RuntimeError('remote data exists without local relay journal')
        state = {'run_id': RUN, 'cursor': 0, 'generations': [], 'pending': None}
        save(state)
    while True:
        try:
            st = SOURCE.stat()
            name = f'preserved-{st.st_dev}-{st.st_ino}.bin'
            target = ROOT / name
            if not target.exists():
                os.link(SOURCE, target)
            if not any(g['file'] == name for g in state['generations']):
                state['generations'].append({'file': name, 'offset': 0})
                save(state)
            pending = state['pending']
            if pending is None:
                for g in state['generations']:
                    size = (ROOT / g['file']).stat().st_size // 32 * 32
                    if size < g['offset']:
                        raise RuntimeError('source generation was truncated')
                    if size == g['offset']:
                        continue
                    with (ROOT / g['file']).open('rb') as f:
                        f.seek(g['offset'])
                        data = f.read(min(32 if state['cursor'] == 0 else 65536, size-g['offset']))
                    check_records(data)
                    atomic(ROOT / 'pending.bin', data)
                    state['pending'] = pending = {'file': g['file'], 'local_offset': g['offset'],
                        'offset': state['cursor'], 'length': len(data),
                        'sha256': hashlib.sha256(data).hexdigest()}
                    save(state)
                    break
            if pending is None:
                state['checked_at'] = time.time()
                save(state)
                time.sleep(10)
                continue
            data = (ROOT / 'pending.bin').read_bytes()
            if len(data) != pending['length'] or hashlib.sha256(data).hexdigest() != pending['sha256']:
                raise RuntimeError('pending journal digest mismatch')
            reply = request(key, 'POST', f"/v1/runs/{RUN}/dps?offset={pending['offset']}", data)
            expected = pending['offset'] + len(data)
            if reply['cursor'] != expected:
                raise RuntimeError('unexpected remote cursor; preserve pending journal')
            for g in state['generations']:
                if g['file'] == pending['file']:
                    g['offset'] = pending['local_offset'] + len(data)
            state.update(cursor=expected, pending=None, acknowledged_at=time.time())
            save(state)
            print(json.dumps({'event': 'acknowledged', 'run_id': RUN, 'cursor': expected,
                              'sha256': pending['sha256'], 'bytes': len(data)}), flush=True)
            time.sleep(0.3)
            if expected == 32:
                time.sleep(90)
        except Exception as exc:
            print(json.dumps({'event': 'retry_preserved', 'error': str(exc), 'cursor': state['cursor']}), flush=True)
            time.sleep(20)


if __name__ == '__main__':
    main()
