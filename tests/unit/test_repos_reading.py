# This file was generated with the assistance of an AI coding tool.
"""An old version's file out of the storage and back out of git (ADR 0030, F94 c, fas 5.6): the round takes a file
only when its repository holds the same bytes and it has not been written for the days, and a reader puts it back
byte for byte."""

from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from opencde.repos.git import Git
from opencde.repos.reading import prune, restore
from opencde.repos.writer import prepare
from opencde.storage import LocalStorage, make_key

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
DATA = b"ISO-10303-21;\r\nHEADER;\r\nENDSEC;\r\nDATA;\r\n#1= IFCWALL($);\r\nENDSEC;\r\nEND-ISO-10303-21;\r\n"


def _version(tmp_path: Path) -> tuple[Git, LocalStorage, dict[str, Any]]:
    """A version of an IFC file in the storage and as a commit on its project's main."""
    git, storage = Git(tmp_path / "git"), LocalStorage(tmp_path / "storage")
    project_id, document_id = str(uuid.uuid4()), str(uuid.uuid4())
    key = make_key("documents", str(uuid.uuid4()))
    storage.path(key).parent.mkdir(parents=True, exist_ok=True)
    storage.path(key).write_bytes(DATA)

    async def commit() -> str:
        made = await prepare(
            git, project_id=project_id, path=storage.path(key), file_name="m.ifc", title="t", author="a", author_id=None
        )
        assert made is not None
        sha = await made.make(
            head=None, document_id=document_id, version_index=1, version_number="1", created="2026-11-27T05:00:00Z"
        )
        await made.move(new=sha, old=None)
        return sha

    row = {
        "project_id": project_id,
        "document_id": document_id,
        "file_name": "m.ifc",
        "storage_key": key,
        "commit": asyncio.run(commit()),
    }
    return git, storage, row


def _pages(rows: list[dict[str, Any]]) -> Any:
    async def candidates(offset: int, limit: int) -> list[dict[str, Any]]:
        return rows[offset : offset + limit]

    return candidates


def test_an_old_file_goes_once_git_holds_it_and_comes_back_byte_for_byte(tmp_path: Path) -> None:
    git, storage, row = _version(tmp_path)
    path = storage.path(row["storage_key"])
    assert asyncio.run(prune(git, storage, 1, _pages([row]))).removed == 0, "written within the day"
    old = time.time() - 3 * 86_400
    os.utime(path, (old, old))
    done = asyncio.run(prune(git, storage, 1, _pages([row])))
    assert (done.removed, done.kept) == (1, 0) and not path.exists()
    assert asyncio.run(restore(git, storage, **row)), "read back out of the version's commit"
    assert path.read_bytes() == DATA, "the bytes as uploaded, line ends and all"
    assert not list(path.parent.glob("*.tmp")), "nothing half written is left"
    assert asyncio.run(prune(git, storage, 1, _pages([row]))).removed == 0, "read again: kept the days anew"


def test_a_file_whose_bytes_git_does_not_hold_stays(tmp_path: Path) -> None:
    git, storage, row = _version(tmp_path)
    path = storage.path(row["storage_key"])
    path.write_bytes(DATA + b"\n")  # not the commit's bytes any more
    old = time.time() - 3 * 86_400
    os.utime(path, (old, old))
    done = asyncio.run(prune(git, storage, 1, _pages([row, {**row, "commit": "not a commit"}])))
    assert (done.removed, done.kept) == (0, 1) and path.exists(), "and a commit that is no sha is never asked"


def test_a_file_that_cannot_come_back_says_so(tmp_path: Path) -> None:
    git, storage, row = _version(tmp_path)
    storage.path(row["storage_key"]).unlink()
    assert not asyncio.run(restore(None, storage, **row)), "no repositories"
    assert not asyncio.run(restore(git, storage, **{**row, "commit": None})), "no commit"
    assert not asyncio.run(restore(git, storage, **{**row, "commit": "0" * 40})), "no such commit"
    assert not asyncio.run(restore(git, storage, **{**row, "file_name": "m.pdf"})), "a file that is not text"
