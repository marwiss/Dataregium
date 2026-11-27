# This file was generated with the assistance of an AI coding tool.
"""The repositories' upkeep in the cleaner's round (ADR 0030, UL-48, fas 5.2).

The server keeps its repositories itself (``gc.auto=0``): each round it takes away what a deletion or an
interrupted making left (``.trash-*``, and ``.new-*`` and ``index-*`` older than an hour), and repacks, one project
at a time, a repository whose loose objects have grown past ``LOOSE``, with a memory window and a time limit of its
own, pruning then the objects that no ref reaches and that are older than a day, such as those of a write whose
transaction rolled back. A proposal's branch goes when its trial is no longer open (``open_among``): approved, or
replaced by a new trial, or gone with its topic; a conflict's branch when its ``Dataregium-Expires-At`` has passed.
What fails is logged and tried again in a later round.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from opencde.logging import get_logger
from opencde.repos.git import Git, GitError, conflict_ref, proposal_ref, remove_tree
from opencde.repos.writer import trailers
from opencde.timeutil import now_iso

if TYPE_CHECKING:
    from pathlib import Path

log = get_logger(__name__)

LOOSE = 1000  # the loose objects past which a repository is repacked
REPACK_SECONDS = 1800.0
WINDOW_MEMORY = "256m"  # what a repack may hold for its delta window, per thread (one thread)
LEFT_SECONDS = 3600.0  # how old a half-made repository is before it is taken away
_COUNT = re.compile(rb"^count: ([0-9]+)$", re.MULTILINE)


@dataclass
class Upkept:
    removed: int = 0  # folders a deletion or an interrupted making left
    repacked: int = 0
    branches: int = 0  # the proposals' branches taken away
    conflicts: int = 0  # the conflicts' branches that expired


async def upkeep(git: Git, open_among: Callable[[list[str]], Awaitable[set[str]]] | None = None) -> Upkept:
    """One round of upkeep over every repository; ``open_among`` tells the proposals whose trials are open."""
    done = Upkept()
    for leftover in _leftovers(git.root):
        try:
            remove_tree(leftover)
            done.removed += 1
        except OSError:
            log.warning("project_repo_left", folder=leftover.name)
    for project_id in git.projects():
        repository = git.repository(project_id)
        try:
            if open_among is not None and (branches := await git.proposals(repository)):
                still = await open_among(sorted(branches))
                for proposal_id, commit in branches.items():
                    if proposal_id not in still:
                        await git.delete_ref(repository, proposal_ref(proposal_id), commit)
                        done.branches += 1
            now = now_iso()
            for conflict_id, commit in (await git.conflicts(repository)).items():
                expires_at = trailers(await git.message(repository, commit)).get("Expires-At")
                if expires_at is None or expires_at <= now:
                    await git.delete_ref(repository, conflict_ref(conflict_id), commit)
                    done.conflicts += 1
            counted = await git.run(repository, "count-objects", "-v")
            loose = _COUNT.search(counted)
            if loose is None or int(loose.group(1)) <= LOOSE:
                continue
            long = Git(git.root, git.executable, REPACK_SECONDS)
            await long.run(repository, "-c", f"pack.windowMemory={WINDOW_MEMORY}", "repack", "-d", "-q")
            await git.run(repository, "prune", "--expire=1.day.ago")
            done.repacked += 1
        except GitError as exc:
            log.warning("project_repo_upkeep_failed", project_id=project_id, error=str(exc)[:200])
    return done


def _leftovers(root: Path) -> list[Path]:
    if not root.is_dir():
        return []
    now = datetime.now(UTC).timestamp()
    found = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        if entry.name.startswith(".trash-") or (
            entry.name.startswith((".new-", "index-")) and now - entry.stat().st_mtime > LEFT_SECONDS
        ):
            found.append(entry)
    return found
