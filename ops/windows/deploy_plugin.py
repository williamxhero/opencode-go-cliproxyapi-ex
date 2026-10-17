"""Bounded CPA plugin upgrade with verified backup and detached recovery.
No secrets in manifests or receipts. Run elevated for Windows services.
Use --recover only with an existing transaction manifest.
"""
import argparse
import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import tomllib


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, value):
    p = Path(p)
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temp, p)


def ps(code):
    encoded = base64.b64encode(code.encode('utf-16le')).decode()
    return subprocess.check_output(['powershell.exe', '-NoProfile', '-EncodedCommand', encoded],
                                   timeout=65, text=True, encoding='utf-8', errors='replace').strip()


def service_state(name, state):
    # ServiceController avoids unbounded Stop-Service waits.
    ps("$ErrorActionPreference='Stop'; $s=Get-Service '" + name + "'; "
       + ("if($s.Status -ne 'Stopped'){$s.Stop()}" if state == 'Stopped' else
          "if($s.Status -ne 'Running'){$s.Start()}")
       + "; $s.WaitForStatus('" + state + "',[TimeSpan]::FromSeconds(50))")


def stop(m):
    if m['mode'] == 'service':
        for name in reversed(m['running_services']):
            service_state(name, 'Stopped')
    else:
        import psutil
        p = psutil.Process(m['pid']) if psutil.pid_exists(m['pid']) else None
        if p and abs(p.create_time() - m['process_started']) < 0.1:
            if Path(p.exe()).resolve() != Path(m['exe']).resolve():
                raise RuntimeError('process_identity')
            p.kill()
            p.wait(20)


def start(m):
    if m['mode'] == 'service':
        failures = []
        for name in m['running_services']:
            try:
                service_state(name, 'Running')
            except Exception:
                failures.append(name)
        if failures:
            raise RuntimeError('service_start_failed')
    else:
        import psutil
        with open(Path(m['transaction']) / 'core-output.log', 'ab') as out:
            p = subprocess.Popen([m['exe'], '-config', m['config']], cwd=m['cwd'],
                                 stdin=subprocess.DEVNULL, stdout=out, stderr=out,
                                 creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
        m['pid'] = p.pid
        m['process_started'] = psutil.Process(p.pid).create_time()
        save(m['manifest'], m)


def keys(m):
    if m['mode'] == 'process':
        return 'isolated-spec2-key', 'isolated-spec2-management'
    t = tomllib.loads(Path(m['gui_config']).read_text(encoding='utf-8-sig'))
    def seek(d):
        if isinstance(d, dict):
            for k, v in d.items():
                if k in ('management-secret-key', 'management_secret_key'):
                    return v
                found = seek(v)
                if found:
                    return found
        return None
    management = seek(t)
    # Read only the local access section, not upstream credentials.
    import re
    s = Path(m['config']).read_text(encoding='utf-8')
    section = s.split('\naccess:', 1)[1].split('\nserver:', 1)[0]
    match = re.search(r'api-keys:\s*\n\s*-\s*[\"\x27]?([^\"\x27\r\n]+)', section)
    if not management or not match:
        raise RuntimeError('local_keys_missing')
    return match.group(1).strip(), management


def request(m, path, key):
    c = http.client.HTTPConnection('127.0.0.1', m['port'], timeout=4)
    try:
        c.request('GET', path, headers={'Authorization': 'Bearer ' + key})
        r = c.getresponse()
        body = r.read(2 * 1024 * 1024)
        if r.status != 200:
            raise RuntimeError('health_http')
        return json.loads(body)
    finally:
        c.close()


def healthy(m, require_plugin, seconds=55):
    key, management = keys(m)
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            ids = {x['id'] for x in request(m, '/v1/models', key)['data']}
            if not set(m['baseline_models']).issubset(ids):
                raise RuntimeError('baseline_models_missing')
            if require_plugin:
                import urllib.parse
                d = request(m, '/v0/management/auth-files/models?name=' +
                            urllib.parse.quote(m['auth_name']), management)
                models = {x['id'] for x in d['models']}
                if not set(m['required_plugin_models']).issubset(models):
                    raise RuntimeError('plugin_models_missing')
            return True
        except Exception:
            time.sleep(1)
    return False


def recover(m):
    backup = Path(m['transaction'])
    for item in m['files']:
        if digest(backup / item['backup']) != item['old_hash']:
            raise RuntimeError('backup_hash')
    for attempt in range(3):
        try:
            stop(m)
            for item in m['files']:
                shutil.copy2(backup / item['backup'], item['target'])
                if digest(item['target']) != item['old_hash']:
                    raise RuntimeError('restore_hash')
            start(m)
            if healthy(m, False, 35):
                stable = all(digest(item['target']) == item['old_hash'] for item in m['files'])
                if not stable:
                    raise RuntimeError('restored_files_changed_after_start')
                save(backup / 'result.json', {'outcome': 'rolled_back', 'core_healthy': True,
                                            'attempt': attempt + 1})
                return
        except Exception:
            pass
        time.sleep(2)
    # Last attempt to start is unconditional even when restore/health failed.
    try:
        start(m)
    finally:
        save(backup / 'result.json', {'outcome': 'recovery_failed', 'core_healthy': False})
    raise RuntimeError('recovery_failed')


def main():
    a = argparse.ArgumentParser()
    a.add_argument('manifest')
    a.add_argument('--recover', action='store_true')
    a.add_argument('--watchdog', action='store_true')
    a.add_argument('--force-failure', action='store_true')
    args = a.parse_args()
    m = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
    tx = Path(m['transaction'])
    tx.mkdir(parents=True, exist_ok=True)
    result = tx / 'result.json'
    if args.watchdog:
        # Recover immediately if the independent upgrade process disappears.
        # Windows releases its file lock on process death, preventing races.
        import psutil
        for _ in range(2400):
            if result.exists() and json.loads(result.read_text())['outcome'] in ('upgraded', 'rolled_back'):
                return
            owner = m.get('deploy_pid')
            try:
                alive = owner and abs(psutil.Process(owner).create_time() - m['deploy_started']) < 0.1
            except psutil.Error:
                alive = False
            if not alive:
                break
            time.sleep(1)
        else:
            return  # Never restore files underneath a still-running owner.
        m = json.loads(Path(args.manifest).read_text(encoding='utf-8-sig'))
    # One owner per transaction, including recovery and watchdog invocations.
    import msvcrt
    lock = open(tx / 'transaction.lock', 'a+b')
    lock.seek(0)
    if not lock.read(1):
        lock.write(b'1')
        lock.flush()
    lock.seek(0)
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    if args.recover or args.watchdog:
        recover(m)
        return
    if result.exists():
        raise RuntimeError('transaction_already_used')
    for item in m['files']:
        if digest(item['candidate']) != item['new_hash'] or digest(item['target']) != item['old_hash']:
            raise RuntimeError('preflight_hash')
        shutil.copy2(item['target'], tx / item['backup'])
        if digest(tx / item['backup']) != item['old_hash']:
            raise RuntimeError('backup_hash')
    if not healthy(m, False, 8):
        raise RuntimeError('preflight_health')
    # Pinned pre-operation manifest is enough for watchdog recovery.
    import psutil
    m['deploy_pid'] = os.getpid()
    m['deploy_started'] = psutil.Process().create_time()
    save(m['manifest'], m)
    subprocess.Popen([sys.executable, __file__, args.manifest, '--watchdog'],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        stop(m)
        for item in m['files']:
            shutil.copy2(item['candidate'], item['target'])
            if digest(item['target']) != item['new_hash']:
                raise RuntimeError('replacement_hash')
        start(m)
        if args.force_failure or not healthy(m, True):
            raise RuntimeError('upgrade_health')
        # Brief stability check, including baseline model routes.
        time.sleep(3)
        if not healthy(m, True, 8):
            raise RuntimeError('stability_health')
        save(result, {'outcome': 'upgraded', 'core_healthy': True,
                      'files': [{'target': x['target'], 'sha256': digest(x['target'])} for x in m['files']]})
    except BaseException:
        recover(m)
        return


if __name__ == '__main__':
    main()
