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
from typing import Any


def digest(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def save(p, value):
    p = Path(p)
    temp = p.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2), encoding='utf-8')
    os.replace(temp, p)


def event(m, stage, code='ok'):
    # Fixed stage/error codes only; never exception text or HTTP bodies.
    path = Path(m['transaction']) / 'events.jsonl'
    try:
        with open(path, 'a', encoding='utf-8') as f:
            f.write(json.dumps({'at': time.time(), 'stage': stage, 'code': code}) + '\n')
    except OSError:
        # Preserve the last-resort service recovery path if diagnostics fill the disk.
        pass


def snapshot(m):
    if m['mode'] != 'service':
        return []
    return json.loads(ps("$names=@('LocalCPA'); function Visit($n){$s=Get-Service $n; "
                         "[pscustomobject]@{name=$s.Name;status=[string]$s.Status}; "
                         "foreach($x in $s.DependentServices){Visit $x.Name}}; "
                         "@(Visit 'LocalCPA') | ConvertTo-Json -Compress"))


def services_ready(m):
    if m['mode'] != 'service':
        return True
    actual = {x['name']: x['status'] for x in snapshot(m)}
    expected = {x['name']: x['status'] for x in m.get('service_snapshot', [])}
    return actual == expected


def preflight(m):
    import ctypes
    import re
    if m['mode'] == 'service':
        if not ctypes.windll.shell32.IsUserAnAdmin():
            raise RuntimeError('administrator_required')
        current = snapshot(m)
        actual = {x['name'] for x in current if x['status'] == 'Running'}
        if actual != set(m['running_services']) or m['running_services'][0] != 'LocalCPA':
            raise RuntimeError('service_snapshot_changed')
        if any(not re.fullmatch(r'[A-Za-z0-9_-]+', n) for n in actual):
            raise RuntimeError('invalid_service_name')
        # The pinned WinSW wrapper binds the service to this exact core/config.
        import xml.etree.ElementTree as ET
        wrapper = ET.parse(m['wrapper_xml']).getroot()
        if Path(wrapper.findtext('executable', '')).resolve() != Path(m['exe']).resolve():
            raise RuntimeError('wrapper_executable')
        if str(Path(m['config'])) not in wrapper.findtext('arguments', ''):
            raise RuntimeError('wrapper_configuration')
        m['service_snapshot'] = current
    targets = [str(Path(f['target']).resolve()).lower() for f in m['files']]
    backups = [f['backup'] for f in m['files']]
    if len(set(targets)) != len(targets) or len(set(backups)) != len(backups):
        raise RuntimeError('duplicate_paths')
    for f in m['files']:
        if Path(f['backup']).name != f['backup'] or not re.fullmatch(r'[0-9a-f]{64}', f['old_hash'] + ''):
            raise RuntimeError('invalid_backup')
        p=Path(f['target']);backup=Path(f['backup'])
        if Path(f['backup']).name != f['backup'] or Path(f['candidate']).resolve() == p.resolve() or backup.resolve() in [Path(x['target']).resolve() for x in m['files']]:
            raise RuntimeError('candidate_or_backup_overlap')
        if any(backup.resolve() == Path(x['candidate']).resolve() for x in m['files']):
            raise RuntimeError('candidate_or_backup_overlap')
        p.parent.mkdir(parents=True,exist_ok=True)
    event(m, 'preflight')


def ps(code):
    code = "$ProgressPreference='SilentlyContinue'; " + code
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
        event(m, 'stop_services')
        for name in reversed(m['running_services']):
            try:
                service_state(name, 'Stopped')
            except Exception:
                event(m, 'stop_service', 'failed')
        states = snapshot(m)
        expected_names = set(m['running_services'])
        actual_names = {x['name'] for x in states}
        if actual_names != expected_names or any(x['status'] != 'Stopped' for x in states):
            raise RuntimeError('services_not_stopped')
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
        event(m, 'start_services')
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
    import threading
    import socket
    state: dict[str, Any] = {'response': None, 'sock': None}
    def abort():
        try:
            sock = state['sock']
            if sock is not None:
                sock.shutdown(socket.SHUT_RDWR)
                sock.close()
            elif state['response'] is not None:
                state['response'].close()
            else:
                c.close()
        except OSError:
            pass
    timer = threading.Timer(5, abort)
    timer.daemon = True
    timer.start()
    try:
        c.request('GET', path, headers={'Authorization': 'Bearer ' + key})
        state['sock'] = c.sock
        r = c.getresponse()
        state['response'] = r
        if state['sock'] is None and r.fp is not None:
            state['sock'] = r.fp.raw._sock
        body = r.read(2 * 1024 * 1024)
        if r.status != 200:
            raise RuntimeError('health_http')
        return json.loads(body)
    finally:
        timer.cancel()
        c.close()


def healthy(m, require_plugin, seconds=55):
    end = time.monotonic() + seconds
    key, management = keys(m)
    while time.monotonic() < end:
        try:
            ids = {x['id'] for x in request(m, '/v1/models', key)['data']}
            if not set(m['baseline_models']).issubset(ids):
                raise RuntimeError('baseline_models_missing')
            if require_plugin:
                needed = set(m['required_plugin_models'])
            elif 'original_plugin_models' in m:
                needed = set(m['original_plugin_models'])
            else:
                needed = None
            if needed is not None:
                import urllib.parse
                d = request(m, '/v0/management/auth-files/models?name=' +
                            urllib.parse.quote(m['auth_name']), management)
                models = {x['id'] for x in d['models']}
                if not needed.issubset(models):
                    raise RuntimeError('plugin_models_mismatch')
            return True
        except Exception:
            time.sleep(1)
    return False


def recover(m):
    backup = Path(m['transaction'])
    try:
        event(m, 'recovery')
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
                if healthy(m, False, 35) and services_ready(m):
                    if not all(digest(item['target']) == item['old_hash'] for item in m['files']):
                        raise RuntimeError('restored_files_changed_after_start')
                    save(backup / 'result.json', {'outcome': 'rolled_back', 'core_healthy': True,
                                                'attempt': attempt + 1, 'services': snapshot(m),
                                                'at': time.time()})
                    return 2
            except Exception:
                event(m, 'recovery_attempt', 'failed')
            time.sleep(2)
    except Exception:
        # A missing/corrupt backup must NOT bypass the last start attempt.
        try:
            event(m, 'recovery_backup', 'failed')
        except Exception:
            pass
    try:
        start(m)
    finally:
        save(backup / 'result.json', {'outcome': 'recovery_failed', 'core_healthy': False,
                                    'at': time.time()})
    return 3


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
        save(tx / 'watchdog-ready.json', {'pid': os.getpid(), 'at': time.time()})
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
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return 4
    if args.recover or args.watchdog:
        return recover(m)
    if result.exists():
        raise RuntimeError('transaction_already_used')
    preflight(m)
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
    ready_deadline = time.monotonic() + 10
    while not (tx / 'watchdog-ready.json').exists():
        if time.monotonic() > ready_deadline:
            raise RuntimeError('watchdog_not_ready')
        time.sleep(.1)
    try:
        event(m, 'stopping')
        stop(m)
        event(m, 'replacing')
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
        if not services_ready(m) or not all(digest(f['target']) == f['new_hash'] for f in m['files']):
            raise RuntimeError('final_state_mismatch')
        event(m, 'verified')
        save(result, {'outcome': 'upgraded', 'core_healthy': True, 'at': time.time(),
                      'services': snapshot(m),
                      'files': [{'target': x['target'], 'sha256': digest(x['target'])} for x in m['files']]})
        return 0
    except BaseException:
        return recover(m)


if __name__ == '__main__':
    sys.exit(main())
