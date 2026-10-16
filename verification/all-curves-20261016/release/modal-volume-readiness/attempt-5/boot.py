
import json,os
from pathlib import Path
if int(os.environ['EXPECTED_COUNTER']) == 0:
    # Public filesystem counter, not a real contributor checkpoint or key.
    with Path('/data/generic-state.json').open('x') as output:
        os.fchmod(output.fileno(),0o600)
        output.write('{"counter":0}')
        output.flush();os.fsync(output.fileno())
def prepare_state(mount_name):
    import json
    import os
    from pathlib import Path
    import stat
    import tempfile

    # Modal exposes the trusted /data mount through a provider-created symlink.
    # Resolve that entry point; persisted state children may never be symlinks.
    mount = Path(mount_name).resolve(strict=True)
    state = mount / 'ecc-state'
    marker = mount / '.ecc-state-migration.json'
    names = ('.contributor.lock', 'generic-registration.json', 'generic-state.json', 'state.json',
             'registration.json', 'dps.bin', 'state.ck')

    def regular(path):
        if not stat.S_ISREG(path.lstat().st_mode):
            raise RuntimeError('Modal state must contain regular files only')

    def sync_dir(path):
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    if not mount.is_dir():
        raise RuntimeError('Modal volume mount must be a real directory')
    if state.is_symlink() or (state.exists() and not state.is_dir()):
        raise RuntimeError('Modal state directory must be a real directory')
    # Modal mount-root chown did not permit UID10001 writes in the live probe.
    # An owned private child passed two fresh-container resume checks.
    state.mkdir(mode=0o700, exist_ok=True)
    legacy = [name for name in names if os.path.lexists(mount / name)]
    if marker.exists() or marker.is_symlink():
        regular(marker)
        migration = json.loads(marker.read_text())
        if (not isinstance(migration, list) or not migration
                or any(not isinstance(name, str) for name in migration)
                or len(set(migration)) != len(migration)
                or any(name not in names for name in migration)
                or any(name not in migration for name in legacy)):
            raise RuntimeError('invalid Modal state migration marker')
    elif legacy:
        # Refuse two possible run identities rather than replace either one.
        if any(os.path.lexists(state / name) for name in names):
            raise RuntimeError('both Modal root and child contain worker state; operator recovery required')
        for name in legacy:
            regular(mount / name)
        fd, temporary = tempfile.mkstemp(prefix='.ecc-state-migration-', dir=mount)
        try:
            with os.fdopen(fd, 'w') as output:
                os.fchmod(output.fileno(), 0o600)
                json.dump(legacy, output)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, marker)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        sync_dir(mount)
        migration = legacy
    else:
        migration = []
    for name in migration:
        source, target = mount / name, state / name
        if os.path.lexists(source):
            regular(source)
            if os.path.lexists(target):
                raise RuntimeError('conflicting Modal migration files; operator recovery required')
            os.rename(source, target)
        else:
            regular(target)  # Resume a bootstrap interrupted after this rename.
    if migration:
        sync_dir(state)
        sync_dir(mount)
        marker.unlink()
        sync_dir(mount)
    # Dedicated volume per named worker. Never follow a persisted symlink.
    for path in state.rglob('*'):
        info = path.lstat()
        if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
            raise RuntimeError('Modal state must not contain symlinks or special files')
    if os.getuid() == 0:
        os.chmod(mount, 0o755)
        for path in [*state.rglob('*'), state]:
            os.chown(path, 10001, 10001, follow_symlinks=False)
            os.chmod(path, 0o700 if path.is_dir() else 0o600)
    return str(state)

import os
os.environ['ECC_STATE_DIR'] = prepare_state(os.environ.get('ECC_VOLUME_MOUNT', '/data'))

if os.getuid()==0:
    os.setgroups([]);os.setgid(10001);os.setuid(10001)
import hashlib
os.umask(0o077)
p=Path(os.environ['ECC_STATE_DIR'])/'generic-state.json'
expected=int(os.environ['EXPECTED_COUNTER'])
assert json.loads(p.read_text())['counter']==expected
raw=(json.dumps({'counter':expected+1,'uid':os.getuid(),'gid':os.getgid()},sort_keys=True)+'\n').encode()
tmp=p.with_suffix('.tmp')
with tmp.open('xb') as output:
    output.write(raw);output.flush();os.fsync(output.fileno())
os.replace(tmp,p)
fd=os.open(p.parent,os.O_RDONLY);os.fsync(fd);os.close(fd)
assert p.read_bytes()==raw
info=p.stat()
assert info.st_uid==info.st_gid==10001 and info.st_mode&0o777==0o600
print(json.dumps({'counter':expected+1,'uid':os.getuid(),'gid':os.getgid(),
    'file_uid':info.st_uid,'file_gid':info.st_gid,'file_mode':oct(info.st_mode&0o777),
    'state_directory':str(p.parent),'mount_entry_is_symlink':Path('/data').is_symlink(),
    'legacy_file_remains':Path('/data/generic-state.json').exists(),
    'sha256':hashlib.sha256(raw).hexdigest()}),flush=True)
