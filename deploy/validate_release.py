#!/usr/bin/env python3
"""Materialize trusted Git blobs; compare releases without reading their Git metadata.

The object store supplied to materialize must be a NEW, root-owned store which has
never been writable by the build/service account. verify never invokes Git and
rejects all additional files except the separately built top-level virtualenv.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path, PurePosixPath
import re
import stat
import subprocess
import sys


def trusted_git(repository: Path, *args: str) -> bytes:
    # No inherited Git config, attributes, replacement objects, hooks or helpers.
    return subprocess.check_output(
        ['git', '--no-replace-objects', '-c', 'core.hooksPath=/dev/null',
         '-c', 'core.fsmonitor=false', '--git-dir', str(repository), *args],
        cwd='/', env={'PATH': '/usr/bin:/bin', 'HOME': '/nonexistent',
                      'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null',
                      'GIT_ATTR_NOSYSTEM': '1', 'GIT_NO_REPLACE_OBJECTS': '1'},
    )


def materialize(repository: Path, commit: str, destination: Path) -> None:
    if not re.fullmatch(r'[0-9a-f]{40}', commit):
        raise ValueError('invalid commit identity')
    entries = []
    for record in trusted_git(repository, 'ls-tree', '-rz', commit).split(b'\0'):
        if not record:
            continue
        metadata, raw_path = record.split(b'\t', 1)
        mode, kind, oid = metadata.split(b' ')
        path = PurePosixPath(os.fsdecode(raw_path))
        if (mode not in {b'100644', b'100755'} or kind != b'blob'
                or path.is_absolute() or any(p in {'', '.', '..', '.git'} for p in path.parts)
                or path.parts[0] == '.venv'):
            raise ValueError('unsupported or reserved source entry')
        entries.append((path, mode, oid))
    # Never extract over any existing candidate, symlink, or retained runtime.
    destination.mkdir(mode=0o755, parents=False, exist_ok=False)
    for path, mode, oid in entries:
        target = destination.joinpath(*path.parts)
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        with target.open('xb') as output:
            output.write(trusted_git(repository, 'cat-file', 'blob', oid.decode('ascii')))
        target.chmod(0o755 if mode == b'100755' else 0o644)


def snapshot(root: Path, owner: int, *, allow_venv: bool) -> dict:
    result = {}

    def visit(path: Path, relative: str) -> None:
        info = path.lstat()
        if info.st_uid != owner or info.st_mode & 0o022:
            raise ValueError('release entry has unsafe ownership or permissions')
        if stat.S_ISLNK(info.st_mode):
            raise ValueError('release contains a symlink')
        if stat.S_ISDIR(info.st_mode):
            result[relative] = ('directory',)
            for child in sorted(path.iterdir()):
                name = child.name if not relative else relative + '/' + child.name
                if not relative and child.name == '.venv' and allow_venv:
                    venv = child.lstat()
                    if (not stat.S_ISDIR(venv.st_mode) or venv.st_uid != owner
                            or venv.st_mode & 0o022):
                        raise ValueError('unsafe generated virtualenv directory')
                    # Runtime dependencies execute as the service user only.
                    # Nothing under this directory is consumed by root tools.
                    continue
                if child.name == '.git':
                    raise ValueError('candidate Git metadata is not permitted')
                visit(child, name)
        elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
            with os.fdopen(fd, 'rb') as source:
                opened = os.fstat(source.fileno())
                if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                    raise ValueError('release changed while validating')
                digest = hashlib.file_digest(source, 'sha256').hexdigest()
            result[relative] = ('file', bool(info.st_mode & 0o111), digest)
        else:
            raise ValueError('release contains a special or hardlinked file')

    visit(root, '')
    return result


def verify(reference: Path, candidate: Path, owner: int = 0) -> None:
    if snapshot(reference, owner, allow_venv=False) != snapshot(candidate, owner, allow_venv=True):
        raise ValueError('candidate does not exactly match the trusted commit tree')


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest='command', required=True)
    extract = subparsers.add_parser('materialize')
    extract.add_argument('repository', type=Path)
    extract.add_argument('commit')
    extract.add_argument('destination', type=Path)
    check = subparsers.add_parser('verify')
    check.add_argument('reference', type=Path)
    check.add_argument('candidate', type=Path)
    check.add_argument('--owner', type=int, default=0)
    args = parser.parse_args()
    try:
        if args.command == 'materialize':
            materialize(args.repository, args.commit, args.destination)
        else:
            verify(args.reference, args.candidate, args.owner)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print(f'release validation failed: {error}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
