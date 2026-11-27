# This file was generated with the assistance of an AI coding tool.
"""A git repository per project (ADR 0030, UL-48, fas 5.2): git's command line closed off from its surroundings, and
a version's commit made from the server's own values. The store's part is in ``tests/integration/test_git_repos.py``."""

from __future__ import annotations

import asyncio
import shutil
import uuid
from pathlib import Path

import pytest

from opencde.repos.git import Git, GitError, Person, remove_tree, trash
from opencde.repos.writer import clean, is_text, message_of, prepare, tree_path

needs_git = pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
DOCUMENT = "3f2504e0-4f89-41d3-9a0c-0305e82c3301"


def test_only_the_servers_values_make_a_path() -> None:
    assert tree_path(DOCUMENT.upper(), "Plan.IFC") == f"documents/{DOCUMENT}/model.ifc"
    assert tree_path(DOCUMENT, "notes.md") == f"documents/{DOCUMENT}/notes.md"
    assert tree_path(DOCUMENT, "model.ifczip") is None and tree_path(DOCUMENT, "plan.pdf") is None
    assert tree_path("../etc", "x.txt") is None, "a document id that is no UUID gives no path"
    with pytest.raises(ValueError, match="UUID"):
        Git(Path("git")).repository("../../etc")


def test_a_file_is_text_by_its_kind_and_its_start() -> None:
    assert is_text("m.ifc", b"ISO-10303-21;\nHEADER;") and is_text("m.ifc", b"\xef\xbb\xbf\r\nISO-10303-21;")
    assert not is_text("m.ifc", b"PK\x03\x04") and not is_text("notes.txt", b"text\0more")
    assert is_text("data.csv", b"a,b\n1,2\n") and not is_text("plan.pdf", b"%PDF-1.7")


def test_the_message_holds_the_clients_text_as_data() -> None:
    """Control characters but line breaks go, the title stays on one line, and the trailers come last; an
    option-like or ref-like text is text in the message, never an argument."""
    assert clean("a\x07b\r\nc\u2028d\x1b[31m", 100) == "ab\ncd[31m"
    assert len(clean("x" * 5000, 2000)) == 2000
    message = message_of(
        title="--upload-pack=evil\nrefs/heads/main",
        version_number="2",
        message="../../etc/passwd\n\n-c core.pager=sh",
        document_id=DOCUMENT,
        version_index=2,
        base=1,
    )
    assert message.splitlines()[0] == "--upload-pack=evil refs/heads/main: version 2"
    assert "../../etc/passwd\n\n-c core.pager=sh\n" in message
    assert message.endswith(f"Dataregium-Document: {DOCUMENT}\nDataregium-Version: 2\nDataregium-Base: 1\n")


def test_the_environment_shuts_out_the_systems_and_the_users_configuration(tmp_path: Path) -> None:
    git = Git(tmp_path / "git")
    env = git.environment(tmp_path / "r.git")
    assert env["GIT_CONFIG_NOSYSTEM"] == "1" and Path(env["GIT_CONFIG_GLOBAL"]).read_bytes() == b""
    assert env["GIT_DIR"] == str(tmp_path / "r.git") and env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["LC_ALL"] == "C" and "GIT_EXEC_PATH" not in env and "PERL5LIB" not in env


@needs_git
def test_a_repository_is_made_whole_and_main_moves_only_from_what_it_named(tmp_path: Path) -> None:
    async def run() -> None:
        git = Git(tmp_path / "git")
        project = str(uuid.uuid4())
        repository = await git.ensure(project)
        assert repository == tmp_path / "git" / f"{project}.git" and await git.head(repository) is None
        assert await git.ensure(project) == repository, "made once"
        assert not [p for p in (tmp_path / "git").iterdir() if p.name.startswith(".new-")]
        assert (await git.run(repository, "config", "gc.auto")).strip() == b"0"
        file = tmp_path / "f.txt"
        file.write_bytes(b"one\r\n")
        blob = await git.blob(repository, file)
        tree = await git.tree(repository, None, {f"documents/{DOCUMENT}/notes.txt": blob})
        who = Person("anna", "u1@users.invalid", "2026-11-27T04:00:00Z")
        first = await git.commit(repository, tree, [], who, who, "one\n")
        await git.move_main(repository, first, None)
        assert await git.read(repository, first, f"documents/{DOCUMENT}/notes.txt") == b"one\r\n"
        second = await git.commit(repository, tree, [first], who, who, "two\n")
        with pytest.raises(GitError):
            await git.move_main(repository, second, None), "main exists: it moves only from what it named"
        await git.move_main(repository, second, first)
        assert await git.head(repository) == second
        aside = trash(git.root, repository)
        remove_tree(aside)
        assert not aside.exists() and not repository.exists()

    asyncio.run(run())


@needs_git
def test_a_version_commit_is_made_on_the_head_and_sets_main_back_after_a_lost_transaction(tmp_path: Path) -> None:
    async def run() -> None:
        git = Git(tmp_path / "git")
        project = str(uuid.uuid4())
        notes = tmp_path / "notes.txt"
        notes.write_bytes(b"first\n")
        commit = await prepare(
            git, project_id=project, path=notes, file_name="notes.txt", title="Notes", author="anna", author_id="u1"
        )
        assert commit is not None
        first = await commit.make(
            head=None, document_id=DOCUMENT, version_index=1, version_number="1", created="2026-11-27T04:00:00Z"
        )
        await commit.move(new=first, old=None)
        # A second write moved main and then lost its transaction: the next one sets main back to the store's head.
        stray = await commit.make(
            head=first, document_id=DOCUMENT, version_index=2, version_number="2", created="2026-11-27T04:01:00Z"
        )
        await commit.move(new=stray, old=first)
        again = await commit.make(
            head=first, document_id=DOCUMENT, version_index=2, version_number="2", created="2026-11-27T04:02:00Z"
        )
        assert await git.head(commit.repository) == first, "set back to the head the store holds"
        await commit.move(new=again, old=first)
        log = (await git.run(commit.repository, "log", "--format=%an <%ae>|%s", "main")).decode()
        assert log == "anna <u1@users.invalid>|Notes: version 2\nanna <u1@users.invalid>|Notes: version 1\n"
        pdf = tmp_path / "plan.pdf"
        pdf.write_bytes(b"%PDF-1.7\n\0")
        assert (
            await prepare(git, project_id=project, path=pdf, file_name="plan.pdf", title="P", author="a", author_id="u")
            is None
        )
        assert (
            await prepare(
                None, project_id=project, path=notes, file_name="notes.txt", title="N", author="a", author_id="u"
            )
            is None
        )

    asyncio.run(run())


def test_the_doctor_says_why_no_repositories_are_written(tmp_path: Path) -> None:
    from pydantic import SecretStr

    from opencde.admin.doctor import git_check
    from tests.conftest import test_settings

    graph = test_settings(tmp_path, git_enabled=True)
    assert graph.store_url is None and asyncio.run(git_check(graph)).detail_key == "git_needs_postgres"
    postgres = test_settings(
        tmp_path,
        git_enabled=True,
        git_path=str(tmp_path / "no-git"),
        store_url="postgresql://u@127.0.0.1:9/d",
        store_password=SecretStr("x" * 20),
    )
    missing = asyncio.run(git_check(postgres))
    assert (missing.ok, missing.detail_key) == (False, "git_missing")
    if shutil.which("git"):
        found = asyncio.run(git_check(postgres.model_copy(update={"git_path": "git"})))
        assert found.ok and found.params["version"].startswith("git version")


@needs_git
def test_a_repository_that_lost_the_stores_head_goes_on_from_its_main(tmp_path: Path) -> None:
    """A restore without its repositories leaves the store a head the repository does not have: the next version
    starts the history again rather than failing; and a main that is gone comes back at the store's head."""

    async def run() -> None:
        git = Git(tmp_path / "git")
        project = str(uuid.uuid4())
        notes = tmp_path / "notes.txt"
        notes.write_bytes(b"one\n")
        args = {"git": git, "project_id": project, "path": notes, "file_name": "notes.txt", "title": "N", "author": "a"}
        commit = await prepare(**args, author_id="u")  # ty: ignore[invalid-argument-type]
        assert commit is not None
        made = {"document_id": DOCUMENT, "version_number": "1", "created": "2026-11-27T04:00:00Z"}
        first = await commit.make(head=None, version_index=1, **made)
        await commit.move(new=first, old=None)
        await git.run(commit.repository, "update-ref", "-d", "refs/heads/main", first)
        again = await prepare(**args, author_id="u")  # ty: ignore[invalid-argument-type]
        assert again is not None
        second = await again.make(head=first, version_index=2, **made)
        assert again.parent == first and await git.head(again.repository) == first, "main back at the store's head"
        await again.move(new=second, old=first)
        remove_tree(commit.repository)
        lost = await prepare(**args, author_id="u")  # ty: ignore[invalid-argument-type]
        assert lost is not None
        third = await lost.make(head=second, version_index=3, **made)
        assert lost.parent is None
        await lost.move(new=third, old=second)
        assert await git.head(lost.repository) == third

    asyncio.run(run())


@needs_git
def test_a_repository_goes_into_a_bundle_and_comes_back_whole(tmp_path: Path) -> None:
    async def run() -> None:
        git = Git(tmp_path / "git")
        project = str(uuid.uuid4())
        empty = str(uuid.uuid4())
        await git.ensure(empty)
        notes = tmp_path / "notes.txt"
        notes.write_bytes(b"one\n")
        commit = await prepare(
            git, project_id=project, path=notes, file_name="notes.txt", title="N", author="a", author_id="u"
        )
        assert commit is not None
        first = await commit.make(
            head=None, document_id=DOCUMENT, version_index=1, version_number="1", created="2026-11-27T04:00:00Z"
        )
        await commit.move(new=first, old=None)
        assert git.projects() == sorted([project, empty])
        bundle = tmp_path / "out.bundle"
        assert not await git.bundle(empty, tmp_path / "empty.bundle"), "a repository without a commit has nothing"
        assert await git.bundle(project, bundle)
        again = Git(tmp_path / "again")
        restored = await again.unbundle(project, bundle)
        assert await again.head(restored) == first
        assert await again.read(restored, first, f"documents/{DOCUMENT}/notes.txt") == b"one\n"
        assert (await again.run(restored, "remote")).strip() == b"", "no remote left behind"
        with pytest.raises(GitError, match="already"):
            await again.unbundle(project, bundle)
        broken = tmp_path / "broken.bundle"
        broken.write_bytes(b"# v2 git bundle\nnot one\n")
        with pytest.raises(GitError):
            await again.unbundle(str(uuid.uuid4()), broken)

    asyncio.run(run())
