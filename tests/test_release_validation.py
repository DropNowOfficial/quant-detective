"""Real, offline Git attacks against the deployment source boundary."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(os.name == 'nt', reason='POSIX deployment ownership contract')

HELPER = Path('deploy/validate_release.py').resolve()


def git(repo, *args):
    return subprocess.run(['git', '-C', str(repo), *args], check=True,
                          capture_output=True, text=True).stdout.strip()


def tree(tmp_path):
    assert HELPER.is_file(), "reviewed exact-tree validator is required"
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Offline test')
    git(repo, 'config', 'user.email', 'offline@example.invalid')
    (repo / 'deploy').mkdir()
    (repo / 'deploy/service').write_text('ExecStart=/reviewed/program\n')
    (repo / '.gitignore').write_text('__pycache__/\nsitecustomize.py\n.venv/\n')
    (repo / '.gitattributes').write_text('deploy/service export-ignore\n')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'trusted snapshot')
    sha = git(repo, 'rev-parse', 'HEAD')
    reference = tmp_path / 'reference'
    result = subprocess.run([sys.executable, str(HELPER), 'materialize', str(repo / '.git'), sha,
                             str(reference)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    candidate = tmp_path / 'candidate'
    shutil.copytree(reference, candidate)
    return repo, reference, candidate, sha


def validate(reference, candidate):
    return subprocess.run([sys.executable, str(HELPER), 'verify', str(reference), str(candidate),
                           '--owner', str(os.getuid())], capture_output=True, text=True)


def test_materialization_uses_exact_blobs_ignoring_archive_attributes(tmp_path):
    repo, reference, candidate, sha = tree(tmp_path)
    assert (reference / 'deploy/service').read_text() == 'ExecStart=/reviewed/program\n'
    assert validate(reference, candidate).returncode == 0


@pytest.mark.parametrize('attack', ['fsmonitor', 'assume_unchanged', 'replacement',
                                  'ignored_python', 'untracked', 'symlink', 'hardlink', 'writable'])
def test_untrusted_candidate_state_cannot_bypass_exact_snapshot(tmp_path, attack):
    repo, reference, candidate, sha = tree(tmp_path)
    marker = tmp_path / 'executed-as-validator'
    if attack in {'fsmonitor', 'assume_unchanged', 'replacement'}:
        shutil.copytree(repo / '.git', candidate / '.git')
    if attack == 'fsmonitor':
        hook = tmp_path / 'hook'
        hook.write_text(f'#!/bin/sh\ntouch "{marker}"\n')
        hook.chmod(0o755)
        git(candidate, 'config', 'core.fsmonitor', str(hook))
    elif attack == 'assume_unchanged':
        git(candidate, 'update-index', '--assume-unchanged', 'deploy/service')
        (candidate / 'deploy/service').write_text('ExecStart=/unreviewed/root-program\n')
        assert git(candidate, 'diff', '--name-only', 'HEAD') == ''
    elif attack == 'replacement':
        (candidate / 'deploy/service').write_text('ExecStart=/unreviewed/root-program\n')
        git(candidate, 'add', '.')
        git(candidate, 'commit', '-m', 'different tree')
        replacement = git(candidate, 'rev-parse', 'HEAD')
        git(candidate, 'replace', sha, replacement)
        git(candidate, 'reset', '--soft', sha)
        assert git(candidate, 'diff', '--name-only', 'HEAD') == ''
    elif attack == 'ignored_python':
        (candidate / 'sitecustomize.py').write_text('raise RuntimeError("injected")\n')
    elif attack == 'untracked':
        (candidate / 'runtime').mkdir()
        (candidate / 'runtime/code.py').write_text('untrusted\n')
    elif attack == 'symlink':
        (candidate / 'deploy/service').unlink()
        (candidate / 'deploy/service').symlink_to(reference / 'deploy/service')
    elif attack == 'hardlink':
        (candidate / 'deploy/service').unlink()
        os.link(reference / 'deploy/service', candidate / 'deploy/service')
    else:
        (candidate / 'deploy/service').chmod(0o666)
    result = validate(reference, candidate)
    assert result.returncode != 0
    assert not marker.exists(), 'candidate Git config must never be executed'


def test_only_generated_venv_is_allowed_outside_commit_tree(tmp_path):
    _, reference, candidate, _ = tree(tmp_path)
    (candidate / '.venv/bin').mkdir(parents=True)
    (candidate / '.venv/bin/python').symlink_to(sys.executable)
    assert validate(reference, candidate).returncode == 0
    (candidate / '__pycache__').mkdir()
    (candidate / '__pycache__/sitecustomize.pyc').write_bytes(b'untrusted')
    assert validate(reference, candidate).returncode != 0


@pytest.mark.parametrize('reserved', ['.venv', 'symlink'])
def test_materialization_rejects_reserved_runtime_or_symlink_entries(tmp_path, reserved):
    assert HELPER.is_file(), "reviewed exact-tree validator is required"
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-b', 'main')
    git(repo, 'config', 'user.name', 'Offline test')
    git(repo, 'config', 'user.email', 'offline@example.invalid')
    if reserved == '.venv':
        (repo / '.venv').mkdir()
        (repo / '.venv/python').write_text('reserved')
    else:
        (repo / 'service').symlink_to('/tmp/external')
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', 'unsupported source')
    result = subprocess.run([sys.executable, str(HELPER), 'materialize', str(repo / '.git'),
                             git(repo, 'rev-parse', 'HEAD'), str(tmp_path / 'target')],
                            capture_output=True, text=True)
    assert result.returncode != 0
