"""Offline updater transactions: real Git/local files; no root/network/systemd."""
from pathlib import Path
import json
import os
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='POSIX deployment ownership contract')


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


@pytest.mark.parametrize('failure,expected', [('', 0), ('stop', 4), ('install', 4),
    ('restart', 4), ('health', 4), ('reconcile', 4), ('rollback', 7),
    ('rollback_stale', 7), ('rollback_health', 7), ('rollback_legacy_checker', 7), ('build', 6), ('identity', 3),
    ('source_tamper', 6), ('ignored_runtime', 6), ('reused_metadata', 0),
    ('current_valid', 0), ('current_corrupt', 5), ('repair_same', 0)])
def test_activation_failures_restore_previous_release_and_operational_files(tmp_path, failure, expected):
    root = tmp_path.as_posix()
    if os.name == 'nt':
        pytest.skip('POSIX ownership and runuser deployment contract')
    upstream = tmp_path / 'upstream'
    upstream.mkdir()
    git(upstream, 'init', '-b', 'main')
    git(upstream, 'config', 'user.name', 'Offline fixture')
    git(upstream, 'config', 'user.email', 'offline@example.invalid')
    (upstream / 'deploy/systemd').mkdir(parents=True)
    shutil.copyfile('deploy/validate_release.py', upstream / 'deploy/validate_release.py')
    (upstream / '.gitignore').write_text('.venv/\n__pycache__/\nsitecustomize.py\n')
    for name in ('live.service', 'update.service', 'update.timer', 'watchdog.service',
                 'watchdog.timer', 'github-fallback.service', 'github-fallback.timer'):
        (upstream / ('deploy/systemd/quant-detective-' + name)).write_text('new-unit\n')
    (upstream / 'deploy/update-production.sh').write_text('new-updater\n')
    (upstream / 'deploy/healthcheck.py').write_text('# health fixture\n')
    git(upstream, 'add', '.')
    git(upstream, 'commit', '-m', 'reviewed fixture')
    sha = git(upstream, 'rev-parse', 'HEAD')
    release_root = tmp_path / 'app'
    previous = release_root / 'releases' / (sha + '.existing' if failure in {'current_valid', 'current_corrupt', 'repair_same'} else 'b' * 40)
    if failure in {'current_valid', 'current_corrupt', 'repair_same'}:
        shutil.copytree(upstream, previous, ignore=shutil.ignore_patterns('.git'))
    (previous / '.venv/bin').mkdir(parents=True)
    health_program = r'''#!/usr/bin/env bash
if [[ "${1:-}" = "-m" ]]; then exit 0; fi
if [[ "$0" = *"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"* ]]; then
  if [[ "$QD_FAULT" = rollback_legacy_checker ]]; then
    [[ "$1" = *"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"* ]]
  else
    [[ "$QD_FAULT" != rollback_health ]]
  fi
else
  [[ "$QD_FAULT" != health && "$QD_FAULT" != rollback_stale && "$QD_FAULT" != rollback_health && "$QD_FAULT" != rollback_legacy_checker ]]
fi
'''
    (previous / '.venv/bin/python').write_text(health_program)
    (previous / '.venv/bin/python').chmod(0o755)
    units = tmp_path / 'units'
    units.mkdir()
    for name in ('live.service', 'update.service', 'update.timer', 'watchdog.service',
                 'watchdog.timer', 'github-fallback.service', 'github-fallback.timer'):
        (units / ('quant-detective-' + name)).write_text('previous-unit\n')
    (tmp_path / 'sbin').mkdir()
    (tmp_path / 'sbin/quant-detective-update').write_text('previous-updater\n')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/github-fallback.env').write_text('untouched-placeholder\n')
    (tmp_path / 'state.json').write_text(json.dumps({'run_id': 'old-run', 'mode': 'DEGRADED_PUBLIC_ONLY'}))
    (release_root / 'current').symlink_to(previous)
    (tmp_path / 'health-program').write_text(health_program)
    (tmp_path / 'uv').write_text(r'''#!/usr/bin/env bash
[[ "$QD_FAULT" != build ]] || exit 1
project="${@: -1}"
mkdir -p "$project/.venv/bin"
cp "$QD_ROOT/health-program" "$project/.venv/bin/python"
chmod 755 "$project/.venv/bin/python"
if [[ "$QD_FAULT" = source_tamper ]]; then echo evil-unit > "$project/deploy/systemd/quant-detective-live.service"; fi
if [[ "$QD_FAULT" = ignored_runtime ]]; then echo untrusted > "$project/sitecustomize.py"; fi
''')
    (tmp_path / 'uv').chmod(0o755)
    reused = release_root / 'releases' / sha
    if failure == 'current_corrupt':
        shutil.copytree(upstream / '.git', previous / '.git')
        git(previous, 'config', 'core.fsmonitor', 'touch ' + str(tmp_path / 'root-executed-hook'))
        (previous / 'sitecustomize.py').write_text('untrusted runtime\n')
    if failure == 'reused_metadata':
        shutil.copytree(upstream, reused)
        hook = tmp_path / 'fsmonitor'
        hook.write_text(f'#!/bin/sh\ntouch "{tmp_path / "root-executed-hook"}"\n')
        hook.chmod(0o755)
        git(reused, 'config', 'core.fsmonitor', str(hook))
        (reused / 'sitecustomize.py').write_text('untrusted runtime\n')
    script = Path('deploy/update-production.sh').read_text()
    substitutions = {'https://github.com/DropNowOfficial/quant-detective.git': str(upstream),
        '/opt/quant-detective': root + '/app',
        '/run/lock/quant-detective-update.lock': root + '/update.lock',
        '/usr/local/bin/uv': root + '/uv', '/usr/local/sbin': root + '/sbin',
        '/etc/systemd/system': root + '/units', '/etc/quant-detective': root + '/config',
        '/var/lib/quant-detective/state.json': root + '/state.json',
        '[ "$(stat -c %u "$UV_BIN")" != "0" ]': f'[ "$(stat -c %u "$UV_BIN")" != "{os.getuid()}" ]',
        'EXPECTED_UID=0': f'EXPECTED_UID={os.getuid()}'}
    for old, new in substitutions.items():
        script = script.replace(old, new)
    (tmp_path / 'updater.sh').write_text(script)
    wrapper = r'''set -Eeuo pipefail
id() { echo 0; }
flock() { return 0; }
chown() { return 0; }
stat() { if [ "$2" = %u ]; then echo "$QD_UID"; else command stat "$@"; fi; }
sleep() { return 0; }
ln() {
  if [[ "${@: -1}" = "$QD_ROOT/app/current" ]]; then
    echo "live current link must be replaced atomically" >&2
    return 97
  fi
  command ln "$@"
}
mv() { printf '%s\n' "$*" >> "$QD_ROOT/link-switch.log"; command mv "$@"; }
runuser() { shift 3; "$@"; }
install() {
  if [ "$QD_FAULT" = install ] && [[ "$*" = *"deploy/systemd/quant-detective-live.service"* ]]; then return 1; fi
  local args=()
  while [ "$#" -gt 0 ]; do
    case "$1" in -o|-g) shift 2;; *) args+=("$1"); shift;; esac
  done
  command install "${args[@]}"
}
restarts=0
systemctl() {
  printf '%s\n' "$*" >> "$QD_ROOT/service.log"
  if [ "$1" = restart ]; then
    restarts=$((restarts+1))
    if [ "$QD_FAULT" = rollback ]; then return 1; fi
    if [ "$QD_FAULT" = restart ] && [ "$restarts" = 1 ]; then return 1; fi
    run_id="run-$restarts"
    if [ "$QD_FAULT" = rollback_stale ] && [ "$restarts" -gt 1 ]; then run_id=old-run; fi
    printf '{"run_id":"%s","mode":"DEGRADED_PUBLIC_ONLY"}' "$run_id" > "$QD_ROOT/state.json"
  fi
  if [ "$QD_FAULT" = stop ] && [ "$1" = stop ] && [ "$2" = quant-detective-watchdog.timer ]; then return 1; fi
  if [ "$QD_FAULT" = reconcile ] && [ "$1" = enable ]; then return 1; fi
  return 0
}
if [[ "$QD_FAULT" = identity ]]; then set -- aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa; fi
if [[ "$QD_FAULT" = repair_same ]]; then set -- --repair; fi
source "$QD_ROOT/updater.sh"
'''
    (tmp_path / 'wrapper.sh').write_text(wrapper)
    result = subprocess.run(['bash', str(tmp_path / 'wrapper.sh')],
        env=os.environ | {'QD_ROOT': root, 'QD_SHA': sha, 'QD_FAULT': failure, 'QD_UID': str(os.getuid())},
        capture_output=True, text=True, timeout=30)
    assert result.returncode == expected, (result.stdout, result.stderr)
    if expected == 0:
        assert (release_root / 'current').resolve().name.startswith(sha)
        assert (units / 'quant-detective-live.service').read_text() == ('previous-unit\n' if failure == 'current_valid' else 'new-unit\n')
        if failure == 'current_valid':
            assert (release_root / 'current').resolve() == previous
        if failure == 'repair_same':
            assert (release_root / 'current').resolve() != previous
        assert not ((release_root / 'current').resolve() / '.git').exists()
    else:
        assert (release_root / 'current').resolve() == previous
        assert (units / 'quant-detective-live.service').read_text() == 'previous-unit\n'
        assert (tmp_path / 'sbin/quant-detective-update').read_text() == 'previous-updater\n'
    if failure in {'stop', 'install', 'restart', 'health', 'reconcile', 'rollback', 'rollback_stale', 'rollback_health', 'rollback_legacy_checker'}:
        assert 'start quant-detective-watchdog.timer' in (tmp_path / 'service.log').read_text()
    if expected == 7:
        assert 'manual recovery required' in result.stderr
    if failure == 'current_corrupt':
        assert not (tmp_path / 'root-executed-hook').exists()
    if failure == 'reused_metadata':
        assert not (tmp_path / 'root-executed-hook').exists()
        assert (release_root / 'current').resolve() != reused
        assert (reused / 'sitecustomize.py').exists()


def test_repair_current_delegates_to_reviewed_updater_and_propagates_failure(tmp_path):
    updater = tmp_path / 'quant-detective-update'
    updater.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path / "args"}"\nexit 7\n')
    updater.chmod(0o755)
    (tmp_path / 'update-production.sh').write_text(updater.read_text())
    script = Path('deploy/repair-current.sh').read_text().replace('/usr/local/sbin/quant-detective-update', str(updater))
    (tmp_path / 'repair.sh').write_text(script)
    result = subprocess.run(['bash', '-c', 'id() { echo 0; }; stat() { if [ "$2" = %u ]; then echo 0; else echo 755; fi; }; file="$1"; shift; source "$file"', '_', str(tmp_path / 'repair.sh')],
                            capture_output=True, text=True)
    assert result.returncode == 7, result.stderr
    assert (tmp_path / 'args').read_text() == '--repair\n'


@pytest.mark.parametrize('kind', ['symlink', 'fifo', 'oversized'])
def test_root_state_reader_refuses_untrusted_file_types_and_unbounded_data(tmp_path, kind):
    if os.name == 'nt':
        pytest.skip('POSIX production state file contract')
    state = tmp_path / 'state.json'
    if kind == 'symlink':
        target = tmp_path / 'elsewhere'
        target.write_text('{"run_id":"must-not-read"}')
        state.symlink_to(target)
    elif kind == 'fifo':
        os.mkfifo(state)
    else:
        state.write_text(json.dumps({'run_id': 'must-not-read', 'large': 'x' * (9 * 1024 * 1024)}))
    script = Path('deploy/update-production.sh').read_text()
    reader = script.split('read_run_id() {', 1)[1].split('\n}', 1)[0]
    reader = reader.replace('/var/lib/quant-detective/state.json', str(state))
    result = subprocess.run(['bash', '-c', reader], capture_output=True, text=True, timeout=2)
    assert result.returncode == 0
    assert result.stdout.strip() == ''


def test_repair_refuses_root_owned_legacy_updater_with_different_reviewed_hash(tmp_path):
    updater = tmp_path / 'legacy-updater'
    marker = tmp_path / 'executed'
    updater.write_text(f'#!/bin/sh\ntouch "{marker}"\nexit 0\n')
    updater.chmod(0o755)
    (tmp_path / 'update-production.sh').write_text('#!/bin/sh\nexit 7\n')
    script = Path('deploy/repair-current.sh').read_text().replace('/usr/local/sbin/quant-detective-update', str(updater))
    (tmp_path / 'repair.sh').write_text(script)
    result = subprocess.run(['bash', '-c', 'id() { echo 0; }; stat() { if [ "$2" = %u ]; then echo 0; else echo 755; fi; }; file="$1"; shift; source "$file"', '_', str(tmp_path / 'repair.sh')],
                            capture_output=True, text=True)
    assert result.returncode == 3
    assert not marker.exists()
