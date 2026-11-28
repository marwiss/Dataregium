# This file was generated with the assistance of an AI coding tool.
"""The working copies whose model a worker holds between an AI's calls (F210 c, ADR 0031).

A working copy of a model of at least ``least_bytes`` gets a worker in ``--serve`` mode (``mcp_worker``) on its first
call, which opens the model once and keeps it, so that the calls after it skip the reading that takes seconds to
minutes for a large model. At most ``most`` are held on the server, a copy beyond them runs a worker per call as a
small one does, and one unused for ``IDLE_SECONDS`` is let go. Since writing a large model takes as long as reading
it, a worker writes its edits when the server asks: after the copy's calls have paused for ``QUIET_SECONDS``, before a
proposal reads the file (``settled``) and when it is let go. A worker that ends or runs out of time is let go, the next call opens the file
again, and the answer says that an edit not yet written may be lost (``CopyLost``). A version's file is never held:
only a working copy, which its user's calls alone reach.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import sys
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ifc_db.files import worker_environment

from opencde.ifcgraph.mcp_worker import CHANGING

IDLE_SECONDS = 15 * 60  # a held model unused this long is let go
QUIET_SECONDS = 30  # a pause in a copy's calls this long has its worker write the edits
REAP_SECONDS = 60  # how often the unused are looked for
LINE_BYTES = 16 * 1024 * 1024  # the longest answer line read: an answer is cut to far less


class CopyLost(RuntimeError):
    """The worker holding a copy ended or ran out of time; its text is the server's, for the AI."""


@dataclass
class _Worker:
    process: asyncio.subprocess.Process
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    used: float = field(default_factory=time.monotonic)
    unwritten: bool = False  # an edit answered and not yet written
    quiet: asyncio.Task[None] | None = None  # the write asked for once the calls pause


class Residents:
    """The held working copies of this server, by the copy's path."""

    def __init__(self, *, most: int, least_bytes: int, memory_mb: int, timeout: float) -> None:
        self.most = most
        self.least_bytes = least_bytes
        self.memory_mb = memory_mb
        self.timeout = timeout
        self._held: dict[str, _Worker] = {}
        self._reaper: asyncio.Task[None] | None = None
        self._closing: set[asyncio.Task[None]] = set()  # the workers let go, writing their last edits
        self._told: dict[str, str] = {}  # a loss found by a write in the background, for the copy's next call

    def wants(self, path: Path) -> bool:
        """Whether a copy is large enough to be held, when there is room."""
        if self.most <= 0:
            return False
        try:
            return path.stat().st_size >= self.least_bytes
        except OSError:
            return False

    def holds(self, path: Path) -> bool:
        return str(path) in self._held

    def __len__(self) -> int:
        return len(self._held)

    async def call(self, path: Path, request: dict[str, Any], starting: asyncio.Semaphore) -> dict[str, Any] | None:
        """The answer of the copy's worker, started under ``starting`` when it is not held yet; None when there is no
        room, and the caller runs a worker of its own."""
        key = str(path)
        if key in self._told:
            raise CopyLost(self._told.pop(key))
        worker = self._held.get(key)
        if worker is not None:
            async with worker.lock:
                if self._held.get(key) is worker:  # not let go while this call waited
                    return await self._ask(key, worker, request)
        await self.reap()
        async with starting:  # the first call reads the model: one such at a time, as a worker per call
            worker = self._held.get(key)
            if worker is None:
                if len(self._held) >= self.most:
                    return None
                worker = await self._start(path)
            async with worker.lock:
                return await self._ask(key, worker, request)

    @contextlib.asynccontextmanager
    async def settled(self, path: Path) -> AsyncGenerator[None]:
        """The copy's file as its worker last wrote it, held still while the caller reads it (a proposal)."""
        worker = self._held.get(str(path))
        if worker is None:
            yield
            return
        async with worker.lock:
            await self._write(str(path), worker)
            yield

    async def drop(self, path: Path) -> None:
        """A closed copy: its worker ends without writing, since the file goes."""
        self._told.pop(str(path), None)
        worker = self._held.pop(str(path), None)
        if worker is not None:
            _cancel(worker)
            await _kill(worker.process)

    async def reap(self, now: float | None = None) -> None:
        """Let go of the workers unused for ``IDLE_SECONDS``; each ends after its last write, in the background."""
        moment = time.monotonic() if now is None else now
        for key, worker in list(self._held.items()):
            if moment - worker.used >= IDLE_SECONDS and not worker.lock.locked():
                self._held.pop(key, None)
                _cancel(worker)
                task = asyncio.create_task(_close(worker.process, self.timeout))
                self._closing.add(task)
                task.add_done_callback(self._closing.discard)

    async def close(self) -> None:
        """At shutdown: every worker after its last write."""
        if self._reaper is not None:
            self._reaper.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._reaper
            self._reaper = None
        held, self._held = list(self._held.values()), {}
        for worker in held:
            _cancel(worker)
        closing = [_close(worker.process, self.timeout) for worker in held]
        await asyncio.gather(*closing, *self._closing, return_exceptions=True)

    async def _start(self, path: Path) -> _Worker:
        command = [
            sys.executable,
            "-m",
            "opencde.ifcgraph.mcp_worker",
            str(path),
            "--serve",
            "--memory-mb",
            str(self.memory_mb),
        ]
        process = await asyncio.create_subprocess_exec(
            *command,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,  # what the parser says may quote the model: never kept
            env=worker_environment(),
            limit=LINE_BYTES,
        )
        worker = _Worker(process)
        self._held[str(path)] = worker
        if self._reaper is None or self._reaper.done():
            self._reaper = asyncio.create_task(self._reap_often())
        return worker

    async def _ask(self, key: str, worker: _Worker, request: dict[str, Any]) -> dict[str, Any]:
        """One request and its answer; a worker that ends or takes too long is let go (``CopyLost``)."""
        process = worker.process
        if process.stdin is None or process.stdout is None:  # started with both piped
            raise RuntimeError("the worker holding the copy has no pipes")
        try:
            process.stdin.write((json.dumps(request, separators=(",", ":")) + "\n").encode("ascii"))
            await process.stdin.drain()
            line = await asyncio.wait_for(process.stdout.readline(), timeout=self.timeout)
        except TimeoutError:
            self._let_go(key, worker)
            await _kill(process)
            raise CopyLost(_lost(f"The call took longer than {self.timeout} s", worker)) from None
        except (OSError, ValueError, ConnectionError):
            line = b""
        if not line:
            self._let_go(key, worker)
            await _kill(process)
            raise CopyLost(_lost("The worker holding the copy ended, in the memory a call has", worker))
        worker.used = time.monotonic()
        answer = dict(json.loads(line))
        if request.get("op") == "ping":
            worker.unwritten = False
        elif request.get("tool") in CHANGING and "error" not in answer:
            worker.unwritten = True
        if worker.unwritten:  # written once the copy's calls pause
            _cancel(worker)
            worker.quiet = asyncio.create_task(self._write_when_quiet(key, worker))
        return answer

    async def _write(self, key: str, worker: _Worker) -> None:
        """The worker writes its edits before it answers; under the worker's lock."""
        if worker.unwritten:
            await self._ask(key, worker, {"op": "ping"})

    async def _write_when_quiet(self, key: str, worker: _Worker) -> None:
        await asyncio.sleep(QUIET_SECONDS)
        async with worker.lock:
            if self._held.get(key) is worker:
                worker.quiet = None  # this task: never cancelled by the answer to its own ping
                try:
                    await self._write(key, worker)
                except CopyLost as exc:  # the copy's next call says so
                    self._told[key] = str(exc)

    def _let_go(self, key: str, worker: _Worker) -> None:
        _cancel(worker)
        if self._held.get(key) is worker:
            del self._held[key]

    async def _reap_often(self) -> None:
        while self._held:
            await asyncio.sleep(REAP_SECONDS)
            await self.reap()


def _cancel(worker: _Worker) -> None:
    if worker.quiet is not None and not worker.quiet.done() and worker.quiet is not asyncio.current_task():
        worker.quiet.cancel()
    worker.quiet = None


def _lost(what: str, worker: _Worker) -> str:
    if worker.unwritten:
        return f"{what}: the copy is as its worker last wrote it, and an edit since then may be lost; read it again"
    return f"{what}; the next call opens the copy again"


async def _close(process: asyncio.subprocess.Process, seconds: float) -> None:
    """End a worker by closing its stdin, after it has written its last edits; killed when it takes too long."""
    if process.stdin is not None:
        with contextlib.suppress(OSError, ConnectionError):
            process.stdin.close()
    try:
        await asyncio.wait_for(process.wait(), timeout=seconds)
    except TimeoutError:
        await _kill(process)


async def _kill(process: asyncio.subprocess.Process) -> None:
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    await process.wait()
