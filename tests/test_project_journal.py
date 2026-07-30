from __future__ import annotations

import contextlib
import ctypes
import errno
import importlib.util
import inspect
import io
import json
import os
import pathlib
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from collections.abc import Callable, Iterator
from unittest import mock


SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "project_journal.py"
SKILL_MD = pathlib.Path(__file__).resolve().parents[1] / "SKILL.md"
README_MD = pathlib.Path(__file__).resolve().parents[1] / "README.md"
OPENAI_YAML = pathlib.Path(__file__).resolve().parents[1] / "agents" / "openai.yaml"
TEMPLATES_MD = (
    pathlib.Path(__file__).resolve().parents[1] / "references" / "templates.md"
)
MIGRATION_PLAYBOOK_MD = (
    pathlib.Path(__file__).resolve().parents[1] / "references" / "migration-playbook.md"
)
SPEC = importlib.util.spec_from_file_location("project_journal", SCRIPT)
assert SPEC is not None
project_journal = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = project_journal
SPEC.loader.exec_module(project_journal)
project_journal._initialize_git_runtime()


def run_git(repo: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def stat_with_gid(value: os.stat_result, gid: int) -> os.stat_result:
    fields = list(value)
    fields[5] = gid
    return os.stat_result(fields)


def stat_with_uid(value: os.stat_result, uid: int) -> os.stat_result:
    fields = list(value)
    fields[4] = uid
    return os.stat_result(fields)


def stat_with_dev(value: os.stat_result, device: int) -> os.stat_result:
    fields = list(value)
    fields[2] = device
    return os.stat_result(fields)


class LegacyUnsupportedPlatform(project_journal.UnsupportedPlatform):
    add_note = None


class LegacyInterrupt(BaseException):
    add_note = None


class ProjectJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    @contextlib.contextmanager
    def default_unblocked_sigint(self) -> Iterator[None]:
        pthread_sigmask = getattr(signal, "pthread_sigmask", None)
        sig_block = getattr(signal, "SIG_BLOCK", None)
        sig_unblock = getattr(signal, "SIG_UNBLOCK", None)
        sig_setmask = getattr(signal, "SIG_SETMASK", None)
        if (
            not callable(pthread_sigmask)
            or not isinstance(sig_block, int)
            or not isinstance(sig_unblock, int)
            or not isinstance(sig_setmask, int)
        ):
            self.skipTest("POSIX SIGINT mask control is unavailable")
        previous_handler = signal.getsignal(signal.SIGINT)
        previous_mask = pthread_sigmask(sig_block, set())
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            pthread_sigmask(sig_unblock, {signal.SIGINT})
            yield
        finally:
            pthread_sigmask(sig_block, {signal.SIGINT})
            signal.signal(signal.SIGINT, previous_handler)
            pthread_sigmask(sig_setmask, previous_mask)

    def exact_source_line(
        self,
        function: object,
        source: str,
        *,
        occurrence: int = 0,
    ) -> int:
        source_lines, first_line = inspect.getsourcelines(function)
        matches = [
            first_line + offset
            for offset, line in enumerate(source_lines)
            if line.strip() == source
        ]
        self.assertGreater(len(matches), occurrence)
        return matches[occurrence]

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_fd_close_signal_fence_preserves_pending_sigint_entry_mask(
        self,
    ) -> None:
        pthread_sigmask = getattr(signal, "pthread_sigmask", None)
        sigpending = getattr(signal, "sigpending", None)
        sigwait = getattr(signal, "sigwait", None)
        sig_block = getattr(signal, "SIG_BLOCK", None)
        sig_setmask = getattr(signal, "SIG_SETMASK", None)
        if (
            not callable(pthread_sigmask)
            or not callable(sigpending)
            or not callable(sigwait)
            or not isinstance(sig_block, int)
            or not isinstance(sig_setmask, int)
        ):
            self.skipTest("POSIX pending-signal controls are unavailable")
        if signal.SIGINT in sigpending():
            self.skipTest("caller already has a pending SIGINT")

        previous_handler = signal.getsignal(signal.SIGINT)
        previous_mask = pthread_sigmask(sig_block, set())
        entry_mask: set[int] | None = None
        try:
            signal.signal(signal.SIGINT, signal.default_int_handler)
            pthread_sigmask(sig_block, {signal.SIGINT})
            entry_mask = {int(value) for value in pthread_sigmask(sig_block, set())}
            os.kill(os.getpid(), signal.SIGINT)
            self.assertIn(signal.SIGINT, sigpending())

            signal_fence = project_journal._block_fd_close_signals()
            self.assertEqual(signal_fence.previous_mask, entry_mask)
            fenced_mask = {int(value) for value in pthread_sigmask(sig_block, set())}
            self.assertTrue(
                {
                    signal.SIGINT,
                    *project_journal._termination_signals(),
                }.issubset(fenced_mask)
            )

            signal_fence.restore()
            restored_mask = {int(value) for value in pthread_sigmask(sig_block, set())}
            self.assertEqual(restored_mask, entry_mask)
            self.assertIn(signal.SIGINT, sigpending())
            self.assertEqual(sigwait({signal.SIGINT}), signal.SIGINT)
        finally:
            pthread_sigmask(sig_block, {signal.SIGINT})
            if signal.SIGINT in sigpending():
                sigwait({signal.SIGINT})
            signal.signal(signal.SIGINT, previous_handler)
            pthread_sigmask(sig_setmask, previous_mask)

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_fd_close_signal_fence_rolls_back_second_step_exception(
        self,
    ) -> None:
        pthread_sigmask = getattr(signal, "pthread_sigmask", None)
        sig_block = getattr(signal, "SIG_BLOCK", None)
        if not callable(pthread_sigmask) or not isinstance(sig_block, int):
            self.skipTest("POSIX signal-mask controls are unavailable")
        entry_mask = {int(value) for value in pthread_sigmask(sig_block, set())}
        interruption = LegacyInterrupt("injected post-mask pre-return interruption")
        call_count = 0

        def apply_then_interrupt(
            how: int,
            signals: set[int],
        ) -> set[int]:
            nonlocal call_count
            call_count += 1
            previous = {int(value) for value in pthread_sigmask(how, signals)}
            if call_count == 2:
                raise interruption
            return previous

        with mock.patch.object(
            project_journal.signal,
            "pthread_sigmask",
            side_effect=apply_then_interrupt,
        ):
            with self.assertRaises(LegacyInterrupt) as raised:
                project_journal._block_fd_close_signals()

        self.assertIs(raised.exception, interruption)
        self.assertEqual(call_count, 3)
        restored_mask = {int(value) for value in pthread_sigmask(sig_block, set())}
        self.assertEqual(restored_mask, entry_mask)

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_fd_close_signal_fence_reports_rollback_failure_as_unverified(
        self,
    ) -> None:
        pthread_sigmask = getattr(signal, "pthread_sigmask", None)
        sig_block = getattr(signal, "SIG_BLOCK", None)
        sig_setmask = getattr(signal, "SIG_SETMASK", None)
        if (
            not callable(pthread_sigmask)
            or not isinstance(sig_block, int)
            or not isinstance(sig_setmask, int)
        ):
            self.skipTest("POSIX signal-mask controls are unavailable")
        entry_mask = {int(value) for value in pthread_sigmask(sig_block, set())}
        interruption = LegacyInterrupt("injected post-mask pre-return interruption")
        rollback_failure = LegacyInterrupt("injected entry-mask rollback interruption")
        call_count = 0

        def fail_after_mask_then_reject_rollback(
            how: int,
            signals: set[int],
        ) -> set[int]:
            nonlocal call_count
            call_count += 1
            if call_count == 3:
                raise rollback_failure
            previous = {int(value) for value in pthread_sigmask(how, signals)}
            if call_count == 2:
                raise interruption
            return previous

        try:
            with mock.patch.object(
                project_journal.signal,
                "pthread_sigmask",
                side_effect=fail_after_mask_then_reject_rollback,
            ):
                with self.assertRaises(LegacyInterrupt) as raised:
                    project_journal._block_fd_close_signals()

            self.assertIs(raised.exception, interruption)
            self.assertEqual(call_count, 3)
            details = "\n".join(getattr(raised.exception, "__notes__", ()))
            self.assertIn(str(rollback_failure), details)
            self.assertIn("thread signal-mask state is unverified", details)
            current_mask = {int(value) for value in pthread_sigmask(sig_block, set())}
            self.assertTrue(
                {
                    signal.SIGINT,
                    *project_journal._termination_signals(),
                }.issubset(current_mask)
            )
        finally:
            pthread_sigmask(sig_setmask, entry_mask)

    def test_bounded_exception_note_propagation_deduplicates_and_caps(
        self,
    ) -> None:
        target = project_journal.UserError("wrapped failure")
        source = OSError(errno.EIO, "source failure")
        source.__notes__ = [
            "duplicate note",
            "duplicate note",
            *(
                f"note {index}: " + ("x" * 5000)
                for index in range(
                    project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS + 2
                )
            ),
        ]

        project_journal._propagate_bounded_exception_notes(
            target,
            source,
            context="wrapped context",
        )
        first_notes = tuple(getattr(target, "__notes__", ()))
        for _ in range(2):
            project_journal._propagate_bounded_exception_notes(
                target,
                source,
                context="wrapped context",
            )
            self.assertEqual(
                tuple(getattr(target, "__notes__", ())),
                first_notes,
            )

        notes = getattr(target, "__notes__", ())
        self.assertLessEqual(
            len(notes),
            project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS,
        )
        self.assertEqual(
            notes.count("wrapped context: duplicate note"),
            1,
        )
        self.assertEqual(len(notes), len(set(notes)))
        self.assertTrue(all(note.startswith("wrapped context: ") for note in notes))
        self.assertTrue(
            all(
                len(note)
                <= (
                    project_journal.MAX_DEFERRED_SIGNAL_REPORT_CHARS
                    + len("…[truncated]")
                )
                for note in notes
            )
        )

    def test_bounded_exception_details_are_stable_across_repeated_calls(
        self,
    ) -> None:
        error = project_journal.UserError("bounded detail failure")
        details = [
            f"detail {index}"
            for index in range(project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS + 3)
        ]

        project_journal._add_exception_details(error, details)
        first_notes = tuple(getattr(error, "__notes__", ()))
        for _ in range(2):
            project_journal._add_exception_details(error, details)
            self.assertEqual(
                tuple(getattr(error, "__notes__", ())),
                first_notes,
            )

        self.assertEqual(
            first_notes,
            (
                *details[: project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS - 1],
                details[-1],
            ),
        )
        self.assertEqual(len(first_notes), len(set(first_notes)))

    def test_fd_close_restore_merge_preserves_close_error_and_restore_evidence(
        self,
    ) -> None:
        close_error = OSError(errno.EIO, "injected close failure")
        underlying_restore_error = OSError(
            errno.EIO,
            "injected ordinary restore failure",
        )
        project_journal._add_exception_detail(
            underlying_restore_error,
            "injected restore source detail",
        )
        fence = project_journal._FdCloseSignalFence(
            pthread_sigmask=mock.Mock(side_effect=underlying_restore_error),
            sig_setmask=signal.SIG_SETMASK,
            previous_mask=set(),
        )

        with self.assertRaises(project_journal.UnsupportedPlatform) as raised:
            fence.restore()
        restore_error = raised.exception
        self.assertIs(restore_error.__cause__, underlying_restore_error)
        wrapper_notes = "\n".join(getattr(restore_error, "__notes__", ()))
        self.assertIn("type=OSError", wrapper_notes)
        self.assertIn(f"errno={errno.EIO} (EIO)", wrapper_notes)
        self.assertIn("injected ordinary restore failure", wrapper_notes)
        self.assertIn("injected restore source detail", wrapper_notes)

        selected = project_journal._merge_fd_close_restore_error(
            close_error,
            restore_error,
            context="test descriptor close",
        )

        self.assertIs(selected, close_error)
        self.assertEqual(close_error.errno, errno.EIO)
        notes = "\n".join(getattr(close_error, "__notes__", ()))
        self.assertIn("type=UnsupportedPlatform", notes)
        self.assertIn("type=OSError", notes)
        self.assertIn(f"errno={errno.EIO} (EIO)", notes)
        self.assertIn("injected ordinary restore failure", notes)
        self.assertIn("injected restore source detail", notes)
        self.assertLessEqual(
            len(getattr(close_error, "__notes__", ())),
            project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS,
        )

    def init_repo(self, name: str = "repo") -> pathlib.Path:
        repo = self.root / name
        repo.mkdir()
        result = run_git(repo, "init")
        self.assertEqual(result.returncode, 0, result.stderr)
        return repo

    def run_cli(
        self,
        *args: str,
        env: dict[str, str] | None = None,
        timeout_seconds: float | None = None,
    ) -> subprocess.CompletedProcess[str]:
        base_env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
        }
        if env is not None:
            base_env.update(env)
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=base_env,
            timeout=timeout_seconds,
        )

    def adoption_status(self, repo: pathlib.Path) -> dict[str, object]:
        result = self.run_cli("adoption-status", "--repo", str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        return json.loads(result.stdout)

    def capture_process(
        self,
        argv: list[str],
        *,
        timeout_seconds: float,
        stdout_limit: int,
        stdout_feed: Callable[[bytes], None] | None = None,
        stdout_finish: Callable[[], None] | None = None,
    ) -> subprocess.CompletedProcess[bytes]:
        return project_journal._capture_bounded_process(
            argv,
            env={"PATH": os.environ.get("PATH", "")},
            timeout_seconds=timeout_seconds,
            stdout_limit=stdout_limit,
            stderr_limit=1024,
            stdout_feed=stdout_feed,
            stdout_finish=stdout_finish,
            stdout_overflow_error="test stdout exceeds limit",
            stderr_overflow_error="test stderr exceeds limit",
            timeout_error="test process timed out",
        )

    def make_fake_git_runtime(
        self,
        name: str,
        *,
        source_text: str | None = None,
    ) -> project_journal._GitRuntime:
        source = self.root / f"{name}-source"
        source.write_text(
            source_text
            if source_text is not None
            else textwrap.dedent(
                """\
                    #!/bin/sh
                    if [ "$1" = "probe" ]; then
                      printf 'safe-launch\\n'
                      exit 0
                    fi
                    printf 'unexpected invocation\\n' >&2
                    exit 91
                    """
            ),
            encoding="utf-8",
        )
        source.chmod(0o755)
        (
            snapshot,
            digest,
            snapshot_identity,
            directory_identity,
            _launcher_kind,
            snapshot_owner,
        ) = project_journal._snapshot_git_executable(
            source,
            expected_source_identity=project_journal._git_source_identity(
                source.stat()
            ),
            deadline=time.monotonic() + 5,
        )
        return project_journal._GitRuntime(
            executable=snapshot,
            source_executable=source,
            launcher_kind="test-script",
            version=(2, 45, 1),
            digest=digest,
            file_identity=snapshot_identity,
            directory_identity=directory_identity,
            snapshot_owner=snapshot_owner,
        )

    def make_native_git_copy(self, name: str) -> pathlib.Path:
        runtime = project_journal._GIT_RUNTIME
        self.assertIsNotNone(runtime)
        assert runtime is not None
        destination = self.root / name
        shutil.copyfile(runtime.source_executable, destination)
        destination.chmod(0o755)
        return destination

    @contextlib.contextmanager
    def isolated_git_runtime_initialization(
        self,
        name: str,
    ) -> Iterator[pathlib.Path]:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        fake_git = self.make_native_git_copy(name)
        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal.shutil,
                "which",
                return_value=str(fake_git),
            ):
                yield fake_git
        finally:
            runtime = project_journal._GIT_RUNTIME
            if runtime is not None and runtime is not old_runtime:
                runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

    def make_terminal_mask_failure_runtime(
        self,
        handled: int,
        *,
        persistent: bool,
    ) -> tuple[project_journal._PosixSignalRuntime, list[int]]:
        actual_runtime = project_journal._load_posix_signal_runtime()
        managed_block_attempts = [0]

        def fail_terminal_block(
            how: int,
            signals: set[int],
        ) -> set[int]:
            if how == actual_runtime.sig_block and signals == {handled}:
                managed_block_attempts[0] += 1
                if managed_block_attempts[0] == 2 or (
                    persistent and managed_block_attempts[0] >= 2
                ):
                    failure = (
                        "persistent terminal block failure"
                        if persistent
                        else "terminal block failure"
                    )
                    raise project_journal.UnsupportedPlatform(failure)
            return actual_runtime.pthread_sigmask(how, signals)

        return (
            project_journal._PosixSignalRuntime(
                pthread_sigmask=fail_terminal_block,
                sigpending=actual_runtime.sigpending,
                sigwait=actual_runtime.sigwait,
                sig_block=actual_runtime.sig_block,
                sig_setmask=actual_runtime.sig_setmask,
            ),
            managed_block_attempts,
        )

    def recovery_evidence(self, reference: str) -> dict[str, object]:
        marker = "recovery_evidence="
        self.assertIn(marker, reference)
        return json.loads(reference.split(marker, 1)[1])

    def exception_traceback_names(self, error: BaseException) -> list[str]:
        names: list[str] = []
        traceback = error.__traceback__
        while traceback is not None:
            names.append(traceback.tb_frame.f_code.co_name)
            traceback = traceback.tb_next
        return names

    def write_journal(
        self,
        repo: pathlib.Path,
        rel: str,
        *,
        entry_id: str,
        title: str,
        status: str,
        updated: str,
        superseded_by: str = "",
    ) -> pathlib.Path:
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            textwrap.dedent(
                f"""\
                ---
                id: {entry_id}
                title: {title}
                status: {status}
                created: 2026-05-01
                updated: {updated}
                branch:
                pr:
                supersedes: []
                superseded_by: {superseded_by}
                ---

                ## Summary

                Test entry.
                """
            ),
            encoding="utf-8",
        )
        return path

    def test_validate_and_generate_index(self) -> None:
        repo = self.init_repo()
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Alpha Work",
            status="active",
            updated="2026-05-05",
        )
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-04-blocked-d4e5f6.md",
            entry_id="20260504-d4e5f6",
            title="Blocked Work",
            status="blocked",
            updated="2026-05-04",
        )
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-03-done-111111.md",
            entry_id="20260503-111111",
            title="Completed Work",
            status="completed",
            updated="2026-05-03",
        )
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-02-old-222222.md",
            entry_id="20260502-222222",
            title="Old Work",
            status="superseded",
            updated="2026-05-02",
            superseded_by="20260505-a1b2c3",
        )

        validate = self.run_cli("validate", "--repo", str(repo))
        self.assertEqual(validate.returncode, 0, validate.stderr)

        generate = self.run_cli(
            "generate",
            "--repo",
            str(repo),
            "--output",
            "docs/project_journal/INDEX.md",
            "--ensure-exclude",
        )
        self.assertEqual(generate.returncode, 0, generate.stderr)

        index = (repo / "docs/project_journal/INDEX.md").read_text(encoding="utf-8")
        self.assertIn("- `active`: 1", index)
        self.assertIn("## Blocked", index)
        self.assertIn("[Alpha Work](2026/05/2026-05-05-alpha-a1b2c3.md)", index)

        exclude = (repo / ".git/info/exclude").read_text(encoding="utf-8")
        self.assertIn("docs/project_journal/INDEX.md", exclude.splitlines())

    def test_validate_skips_custom_generated_index(self) -> None:
        repo = self.init_repo()
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Alpha Work",
            status="active",
            updated="2026-05-05",
        )

        first = self.run_cli(
            "generate",
            "--repo",
            str(repo),
            "--output",
            "docs/project_journal/custom.md",
        )
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_cli(
            "generate",
            "--repo",
            str(repo),
            "--output",
            "docs/project_journal/custom.md",
        )
        self.assertEqual(second.returncode, 0, second.stderr)
        validate = self.run_cli("validate", "--repo", str(repo))
        self.assertEqual(validate.returncode, 0, validate.stderr)

    def test_generated_index_status_distinguishes_missing_and_content(self) -> None:
        root = self.root / "marker-status"
        root.mkdir()
        missing = root / "missing.md"
        ordinary = root / "ordinary.md"
        generated = root / "generated.md"
        ordinary.write_text("---\nid: ordinary\n", encoding="utf-8")
        generated.write_text(
            f"# Project Journal Index\n\n{project_journal.INDEX_GENERATED_LINE}\n",
            encoding="utf-8",
        )

        self.assertEqual(
            project_journal._generated_index_status(missing),
            "missing",
        )
        self.assertEqual(
            project_journal._generated_index_status(ordinary),
            "non-generated",
        )
        self.assertEqual(
            project_journal._generated_index_status(generated),
            "generated",
        )

    def test_generated_index_status_propagates_dangling_symlink(self) -> None:
        dangling = self.root / "dangling.md"
        dangling.symlink_to(self.root / "missing-target")

        with self.assertRaises(project_journal.GeneratedIndexInspectionError) as raised:
            project_journal._generated_index_status(dangling)
        self.assertEqual(raised.exception.errno, errno.ELOOP)

    def test_generated_index_status_bounds_marker_prefix(self) -> None:
        marker = self.root / "oversized-marker.md"
        marker.write_bytes(
            b"# Project Journal Index\n\n"
            + b"x" * (project_journal.MAX_GENERATED_INDEX_MARKER_LINE_BYTES + 1)
        )

        with self.assertRaisesRegex(
            project_journal.GeneratedIndexInspectionError,
            "marker line exceeds",
        ):
            project_journal._generated_index_status(marker)

    def test_generated_index_status_rejects_fifo_without_blocking(self) -> None:
        marker = self.root / "marker-fifo.md"
        os.mkfifo(marker)
        started = time.monotonic()

        with self.assertRaises(project_journal.GeneratedIndexInspectionError) as raised:
            project_journal._generated_index_status(
                marker,
                deadline=started + 1.0,
            )

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(
            raised.exception.errno,
            getattr(errno, "ENXIO", errno.EINVAL),
        )
        self.assertIn("not a regular file", str(raised.exception))

    def test_generated_index_status_consumes_existing_near_deadline(
        self,
    ) -> None:
        marker = self.root / "near-deadline.md"
        marker.write_text("---\n", encoding="utf-8")
        clock = {"now": 100.0}
        actual_read = project_journal.os.read

        def read_past_deadline(fd: int, byte_count: int) -> bytes:
            content = actual_read(fd, byte_count)
            clock["now"] = 101.0
            return content

        with mock.patch.object(
            project_journal.time,
            "monotonic",
            side_effect=lambda: clock["now"],
        ):
            with mock.patch.object(
                project_journal.os,
                "read",
                side_effect=read_past_deadline,
            ):
                with self.assertRaisesRegex(
                    project_journal.GeneratedIndexInspectionError,
                    "shared deadline",
                ):
                    project_journal._generated_index_status(
                        marker,
                        deadline=100.5,
                    )

    def test_generated_index_status_preserves_primary_over_close_failure(
        self,
    ) -> None:
        marker = self.root / "generated-index-primary-close.md"
        marker.write_text("---\n", encoding="utf-8")
        primary = project_journal.GeneratedIndexInspectionLimitExceeded(
            "injected generated-index limit",
            limit_name="generated-index marker bytes",
            limit=1,
            observed=2,
        )
        close_error = OSError(
            errno.EIO,
            "injected generated-index close failure",
        )
        actual_close = project_journal.os.close

        def close_then_fail(fd: int) -> None:
            actual_close(fd)
            raise close_error

        with (
            mock.patch.object(
                project_journal,
                "_read_generated_index_marker_prefix",
                side_effect=primary,
            ),
            mock.patch.object(
                project_journal.os,
                "close",
                side_effect=close_then_fail,
            ),
            self.assertRaises(
                project_journal.GeneratedIndexInspectionLimitExceeded
            ) as raised,
        ):
            project_journal._generated_index_status(marker)

        self.assertIs(raised.exception, primary)
        self.assertEqual(primary.limit_name, "generated-index marker bytes")
        self.assertEqual(primary.limit, 1)
        self.assertEqual(primary.observed, 2)
        notes = "\n".join(getattr(primary, "__notes__", ()))
        self.assertIn("generated-index marker descriptor cleanup failed", notes)
        self.assertIn("errno=5 (EIO)", notes)
        self.assertIn("injected generated-index close failure", notes)

    def test_generated_index_status_wraps_close_only_failure(self) -> None:
        marker = self.root / "generated-index-close-only.md"
        marker.write_text("---\n", encoding="utf-8")
        close_error = OSError(
            errno.EIO,
            "injected generated-index close-only failure",
        )
        actual_close = project_journal.os.close

        def close_then_fail(fd: int) -> None:
            actual_close(fd)
            raise close_error

        with mock.patch.object(
            project_journal.os,
            "close",
            side_effect=close_then_fail,
        ):
            with self.assertRaises(
                project_journal.GeneratedIndexInspectionError
            ) as raised:
                project_journal._generated_index_status(marker)

        self.assertIs(raised.exception.__cause__, close_error)
        self.assertEqual(
            raised.exception.code,
            "generated_index_inspection_failed",
        )
        self.assertEqual(raised.exception.errno, errno.EIO)
        self.assertIn(
            "generated-index marker descriptor cleanup failed",
            str(raised.exception),
        )

    def test_generated_index_status_does_not_consume_ambient_exception(
        self,
    ) -> None:
        marker = self.root / "generated-index-ambient-close.md"
        marker.write_text("---\n", encoding="utf-8")
        ambient = RuntimeError("unrelated outer exception")
        close_error = OSError(
            errno.EIO,
            "injected generated-index ambient close failure",
        )
        actual_close = project_journal.os.close

        def close_then_fail(fd: int) -> None:
            actual_close(fd)
            raise close_error

        try:
            raise ambient
        except RuntimeError:
            with (
                mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=close_then_fail,
                ),
                self.assertRaises(
                    project_journal.GeneratedIndexInspectionError
                ) as raised,
            ):
                project_journal._generated_index_status(marker)

        self.assertIs(raised.exception.__cause__, close_error)
        self.assertEqual(
            raised.exception.code,
            "generated_index_inspection_failed",
        )
        self.assertEqual(raised.exception.errno, errno.EIO)
        self.assertEqual(getattr(ambient, "__notes__", ()), ())

    def test_journal_scan_treats_disappeared_entry_as_missing(self) -> None:
        repo = self.init_repo()
        journal = repo / "docs/project_journal/disappearing.md"
        journal.parent.mkdir(parents=True)
        journal.write_text("---\n", encoding="utf-8")
        original_status = project_journal._generated_index_status

        def disappear(
            path: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> str:
            path.unlink()
            return original_status(path, deadline=deadline)

        with mock.patch.object(
            project_journal,
            "_generated_index_status",
            side_effect=disappear,
        ):
            paths = project_journal._journal_paths(repo)

        self.assertEqual(paths, [])

    def test_adoption_status_rejects_empty_journal_directory(self) -> None:
        repo = self.init_repo()
        (repo / "docs/project_journal").mkdir(parents=True)

        status = self.adoption_status(repo)

        self.assertFalse(status["tracked_journal_adopted"])
        self.assertEqual(status["tracked_non_generated_journal_count"], 0)
        self.assertEqual(status["valid_tracked_journal_count"], 0)

    def test_adoption_status_ignores_exact_journal_root_index_entries(self) -> None:
        for kind in ("file", "symlink", "gitlink"):
            with self.subTest(kind=kind):
                repo = self.init_repo(f"exact-root-{kind}")
                docs = repo / "docs"
                docs.mkdir()
                root = docs / "project_journal"

                if kind == "file":
                    root.write_text("not a journal directory\n", encoding="utf-8")
                    staged = run_git(repo, "add", "--", "docs/project_journal")
                elif kind == "symlink":
                    target = repo / "journal-root-target"
                    target.write_text("not a journal directory\n", encoding="utf-8")
                    root.symlink_to(target)
                    staged = run_git(repo, "add", "--", "docs/project_journal")
                else:
                    staged = run_git(
                        repo,
                        "update-index",
                        "--add",
                        "--info-only",
                        "--cacheinfo",
                        f"160000,{'a' * 40},docs/project_journal",
                    )
                self.assertEqual(staged.returncode, 0, staged.stderr)

                status = self.adoption_status(repo)

                self.assertFalse(status["tracked_journal_adopted"])
                self.assertEqual(status["tracked_non_generated_journal_count"], 0)
                self.assertEqual(status["valid_tracked_journal_count"], 0)

    def test_adoption_status_rejects_generated_index_only_directory(self) -> None:
        repo = self.init_repo()
        generate = self.run_cli(
            "generate",
            "--repo",
            str(repo),
            "--output",
            "docs/project_journal/INDEX.md",
            "--ensure-exclude",
        )
        self.assertEqual(generate.returncode, 0, generate.stderr)
        force_add = run_git(
            repo,
            "add",
            "-f",
            "--",
            "docs/project_journal/INDEX.md",
        )
        self.assertEqual(force_add.returncode, 0, force_add.stderr)

        status = self.adoption_status(repo)

        self.assertFalse(status["tracked_journal_adopted"])
        self.assertEqual(status["tracked_non_generated_journal_count"], 0)
        self.assertEqual(status["valid_tracked_journal_count"], 0)

    def test_adoption_status_requires_valid_tracked_journal_entry(self) -> None:
        repo = self.init_repo()
        journal = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Alpha Work",
            status="active",
            updated="2026-05-05",
        )

        untracked = self.adoption_status(repo)
        self.assertFalse(untracked["tracked_journal_adopted"])

        add = run_git(repo, "add", "--", str(journal.relative_to(repo)))
        self.assertEqual(add.returncode, 0, add.stderr)
        tracked = self.adoption_status(repo)

        self.assertTrue(tracked["tracked_journal_adopted"])
        self.assertEqual(tracked["tracked_non_generated_journal_count"], 1)
        self.assertEqual(tracked["valid_tracked_journal_count"], 1)

    @unittest.skipUnless(
        sys.platform == "darwin" and pathlib.Path("/usr/bin/xcrun").is_file(),
        "Xcode Python 3.9 compatibility regression",
    )
    def test_xcode_python39_runs_real_adoption_status_cli(self) -> None:
        version = subprocess.run(
            ["/usr/bin/xcrun", "python3", "--version"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
        )
        if version.returncode != 0 or not version.stdout.startswith("Python 3.9."):
            self.skipTest(f"Xcode Python 3.9 is unavailable: {version.stdout.strip()}")

        repo = self.init_repo("xcode-python39-adoption")
        journal = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Alpha Work",
            status="active",
            updated="2026-05-05",
        )
        staged = run_git(repo, "add", "--", str(journal.relative_to(repo)))
        self.assertEqual(staged.returncode, 0, staged.stderr)
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONPYCACHEPREFIX": str(self.root / "xcode-python39-cache"),
            "TMPDIR": os.environ.get("TMPDIR", str(self.root)),
        }

        result = subprocess.run(
            [
                "/usr/bin/xcrun",
                "python3",
                str(SCRIPT),
                "adoption-status",
                "--repo",
                str(repo),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["tracked_journal_adopted"])
        self.assertEqual(payload["valid_tracked_journal_count"], 1)

    @unittest.skipUnless(
        sys.platform == "darwin" and pathlib.Path("/usr/bin/xcrun").is_file(),
        "Xcode Python 3.9 inherited SIGCHLD compatibility regression",
    )
    def test_xcode_python39_rejects_inherited_ignored_sigchld_prelaunch(
        self,
    ) -> None:
        resolved = subprocess.run(
            ["/usr/bin/xcrun", "--find", "python3"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        xcode_python = pathlib.Path(resolved.stdout.strip())
        version = subprocess.run(
            [str(xcode_python), "--version"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=5,
        )
        if version.returncode != 0 or not version.stdout.startswith("Python 3.9."):
            self.skipTest(f"Xcode Python 3.9 is unavailable: {version.stdout.strip()}")

        repo = self.init_repo("xcode-python39-ignored-sigchld")
        launcher = self.root / "inherit-ignored-sigchld.py"
        driver = self.root / "ignored-sigchld-driver.py"
        launcher.write_text(
            textwrap.dedent(
                """
                import os
                import signal
                import sys

                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
                os.execv(sys.argv[1], [sys.argv[1], *sys.argv[2:]])
                """
            ).lstrip(),
            encoding="utf-8",
        )
        driver.write_text(
            textwrap.dedent(
                """
                import contextlib
                import importlib.util
                import io
                import json
                import os
                import pathlib
                import signal
                import sys

                script = pathlib.Path(sys.argv[1])
                repo = pathlib.Path(sys.argv[2])
                spec = importlib.util.spec_from_file_location(
                    "ignored_sigchld_project_journal",
                    script,
                )
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                popen_calls = 0
                killpg_calls = 0

                def forbidden_popen(*args, **kwargs):
                    global popen_calls
                    popen_calls += 1
                    raise AssertionError("Popen must not run with ignored SIGCHLD")

                def forbidden_killpg(*args, **kwargs):
                    global killpg_calls
                    killpg_calls += 1
                    raise AssertionError("killpg must not run without a PID fence")

                module.subprocess.Popen = forbidden_popen
                module.os.killpg = forbidden_killpg
                stderr = io.StringIO()
                with contextlib.redirect_stderr(stderr):
                    status = module.main(
                        ["adoption-status", "--repo", str(repo)]
                    )
                try:
                    os.waitpid(-1, os.WNOHANG)
                except ChildProcessError:
                    child_state = "none"
                else:
                    child_state = "unexpected-waitable-child"
                print(
                    json.dumps(
                        {
                            "child_state": child_state,
                            "killpg_calls": killpg_calls,
                            "popen_calls": popen_calls,
                            "sigchld_ignored": (
                                signal.getsignal(signal.SIGCHLD)
                                == signal.SIG_IGN
                            ),
                            "status": status,
                            "stderr": stderr.getvalue(),
                        }
                    )
                )
                """
            ).lstrip(),
            encoding="utf-8",
        )
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONPYCACHEPREFIX": str(self.root / "xcode-python39-cache"),
            "TMPDIR": os.environ.get("TMPDIR", str(self.root)),
        }

        result = subprocess.run(
            [
                sys.executable,
                str(launcher),
                str(xcode_python),
                str(driver),
                str(SCRIPT),
                str(repo),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["sigchld_ignored"])
        self.assertEqual(payload["status"], 1)
        self.assertEqual(payload["popen_calls"], 0)
        self.assertEqual(payload["killpg_calls"], 0)
        self.assertEqual(payload["child_state"], "none")
        self.assertIn(
            "waitable SIGCHLD semantics are required before process launch: "
            "SIGCHLD is ignored",
            payload["stderr"],
        )

    def test_git_policy_removes_ambient_git_control_environment(self) -> None:
        repo = self.init_repo()
        poison = {
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.root / "alternates"),
            "GIT_COMMON_DIR": str(self.root / "common"),
            "GIT_CONFIG": str(self.root / "config"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_VALUE_0": str(self.root / "other-worktree"),
            "GIT_DIR": str(self.root / "other-git-dir"),
            "GIT_EXEC_PATH": str(self.root / "git-exec"),
            "GIT_INDEX_FILE": str(self.root / "other-index"),
            "GIT_NO_LAZY_FETCH": "0",
            "GIT_OBJECT_DIRECTORY": str(self.root / "objects"),
            "GIT_WORK_TREE": str(self.root / "other-worktree"),
        }
        completed_bytes = subprocess.CompletedProcess([], 0, b"", b"")

        with mock.patch.dict(os.environ, poison, clear=False):
            with mock.patch.object(
                project_journal,
                "_capture_bounded_process",
                return_value=completed_bytes,
            ) as capture:
                project_journal._run_git(repo, "rev-parse", "--show-toplevel")

        expected_git_env = {
            key: value
            for key, value in project_journal.SAFE_GIT_ENV.items()
            if key.startswith("GIT_")
        }
        capture.assert_called_once()
        child_env = capture.call_args.kwargs["env"]
        actual_git_env = {
            key: value for key, value in child_env.items() if key.startswith("GIT_")
        }
        self.assertEqual(actual_git_env, expected_git_env)
        self.assertNotIn("GIT_DIR", child_env)
        self.assertNotIn("GIT_INDEX_FILE", child_env)
        self.assertNotIn("GIT_OBJECT_DIRECTORY", child_env)
        self.assertEqual(child_env["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(child_env["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(child_env["GIT_OPTIONAL_LOCKS"], "0")
        self.assertEqual(child_env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(child_env["GIT_ASKPASS"], "")
        argv = capture.call_args.args[0]
        self.assertIn("--no-optional-locks", argv)
        self.assertIn("core.fsmonitor=false", argv)
        self.assertIn(f"core.hooksPath={os.devnull}", argv)
        self.assertIn(f"core.attributesFile={os.devnull}", argv)

    def test_git_runtime_is_fixed_absolute_and_meets_minimum_version(self) -> None:
        runtime = project_journal._GIT_RUNTIME

        self.assertIsNotNone(runtime)
        assert runtime is not None
        self.assertTrue(runtime.executable.is_absolute())
        self.assertEqual(runtime.executable, runtime.executable.resolve())
        self.assertNotEqual(runtime.executable, runtime.source_executable)
        self.assertEqual(runtime.launcher_kind, "native")
        self.assertEqual(runtime.executable.stat().st_mode & 0o777, 0o500)
        self.assertEqual(runtime.executable.parent.stat().st_mode & 0o777, 0o700)
        self.assertGreaterEqual(runtime.version, project_journal.MINIMUM_GIT_VERSION)
        command = project_journal._git_command(
            self.root,
            "rev-parse",
            "--show-toplevel",
        )
        self.assertEqual(command[0], str(runtime.source_executable))

    def test_git_gate_rejects_relative_script_wrapper_with_runtime_evidence(
        self,
    ) -> None:
        fake_bin = self.root / "relative-wrapper-bin"
        fake_bin.mkdir()
        marker = self.root / "relative-wrapper-executed"
        companion = fake_bin / "git-real"
        companion.write_text(
            "#!/bin/sh\n"
            f"printf executed > {shlex.quote(str(marker))}\n"
            "printf 'git version 2.45.1\\n'\n",
            encoding="utf-8",
        )
        companion.chmod(0o755)
        wrapper = fake_bin / "git"
        wrapper.write_text(
            '#!/bin/sh\nexec "$0-real" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(0o755)
        repo = self.root / "relative-wrapper-repo"
        repo.mkdir()

        result = self.run_cli(
            "adoption-status",
            "--repo",
            str(repo),
            env={
                "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
            },
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "selected Git executable bytes identify a script wrapper",
            result.stderr,
        )
        self.assertIn(
            "cannot preserve relative wrapper or interpreter "
            "runtime-location semantics",
            result.stderr,
        )
        self.assertFalse(marker.exists())

    def test_native_git_launch_preserves_source_argv0_for_runtime_prefix(
        self,
    ) -> None:
        installed_runtime = project_journal._require_git_runtime()
        source = installed_runtime.source_executable
        git_env = project_journal._git_environment()
        expected = subprocess.run(
            [str(source), "--exec-path"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=git_env,
            timeout=2,
        )
        self.assertEqual(expected.returncode, 0, expected.stderr)
        self.assertTrue(expected.stdout.strip())
        (
            snapshot,
            digest,
            snapshot_identity,
            directory_identity,
            launcher_kind,
            snapshot_owner,
        ) = project_journal._snapshot_git_executable(
            source,
            expected_source_identity=project_journal._git_source_identity(
                source.stat()
            ),
            deadline=time.monotonic() + 5,
        )
        self.assertEqual(launcher_kind, "native")
        runtime = project_journal._GitRuntime(
            executable=snapshot,
            source_executable=source,
            launcher_kind=launcher_kind,
            version=installed_runtime.version,
            digest=digest,
            file_identity=snapshot_identity,
            directory_identity=directory_identity,
            snapshot_owner=snapshot_owner,
        )

        try:
            result = project_journal._capture_bounded_process(
                [
                    str(runtime.executable),
                    "--exec-path",
                ],
                env=git_env,
                verified_runtime=runtime,
                timeout_seconds=2,
                stdout_limit=4096,
                stderr_limit=4096,
                stdout_overflow_error="runtime-prefix stdout overflow",
                stderr_overflow_error="runtime-prefix stderr overflow",
                timeout_error="runtime-prefix launch timed out",
                operation="runtime-prefix argv0 probe",
            )
        finally:
            runtime.snapshot_owner.cleanup()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.args[0], str(source))
        self.assertEqual(
            result.stdout.strip(),
            expected.stdout.strip(),
        )

    def test_git_gate_executes_bound_snapshot_after_source_replacement_and_rewrite(
        self,
    ) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        self.assertIsNotNone(old_runtime)
        assert old_runtime is not None
        fake_git = self.root / "git"
        malicious_script = textwrap.dedent(
            """\
            #!/bin/sh
            printf 'replaced-source\\n'
            """
        )

        try:
            for mutation in ("replacement", "same-inode-rewrite"):
                with self.subTest(mutation=mutation):
                    shutil.copyfile(old_runtime.source_executable, fake_git)
                    fake_git.chmod(0o755)
                    project_journal._GIT_RUNTIME = None
                    project_journal._GIT_RUNTIME_ERROR = None
                    with mock.patch.object(
                        project_journal.shutil,
                        "which",
                        return_value=str(fake_git),
                    ):
                        project_journal._initialize_git_runtime()
                    runtime = project_journal._GIT_RUNTIME
                    self.assertIsNotNone(runtime)
                    assert runtime is not None

                    if mutation == "replacement":
                        replacement = self.root / "replacement-git"
                        replacement.write_text(malicious_script, encoding="utf-8")
                        replacement.chmod(0o755)
                        os.replace(replacement, fake_git)
                    else:
                        original_inode = fake_git.stat().st_ino
                        fake_git.write_text(malicious_script, encoding="utf-8")
                        fake_git.chmod(0o755)
                        self.assertEqual(fake_git.stat().st_ino, original_inode)

                    result = subprocess.run(
                        [str(project_journal._fixed_git_executable()), "--version"],
                        check=False,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertRegex(result.stdout, r"\Agit version ")
                    runtime.snapshot_owner.cleanup()
        finally:
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

    def test_git_runtime_snapshot_rejects_regular_path_replacement(self) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        actual_open = os.open

        try:
            for phase in ("before-open", "after-open"):
                with self.subTest(phase=phase):
                    fake_git = self.root / f"git-{phase}"
                    fake_git.write_text(
                        "#!/bin/sh\nprintf 'git version 2.45.1\\n'\n",
                        encoding="utf-8",
                    )
                    fake_git.chmod(0o755)
                    resolved_fake_git = fake_git.resolve()
                    attacker = self.root / f"attacker-{phase}"
                    attacker.write_text(
                        "#!/bin/sh\nprintf 'attacker executed\\n'\n",
                        encoding="utf-8",
                    )
                    attacker.chmod(0o755)
                    replaced = False

                    def replace_during_open(
                        path: os.PathLike[str] | str,
                        flags: int,
                        mode: int = 0o777,
                        *,
                        dir_fd: int | None = None,
                    ) -> int:
                        nonlocal replaced
                        if pathlib.Path(path) == resolved_fake_git and not replaced:
                            replaced = True
                            if phase == "before-open":
                                os.replace(attacker, fake_git)
                                return actual_open(
                                    path,
                                    flags,
                                    mode,
                                    dir_fd=dir_fd,
                                )
                            source_fd = actual_open(
                                path,
                                flags,
                                mode,
                                dir_fd=dir_fd,
                            )
                            os.replace(attacker, fake_git)
                            return source_fd
                        return actual_open(path, flags, mode, dir_fd=dir_fd)

                    project_journal._GIT_RUNTIME = None
                    project_journal._GIT_RUNTIME_ERROR = None
                    with mock.patch.object(
                        project_journal.shutil,
                        "which",
                        return_value=str(fake_git),
                    ):
                        with mock.patch.object(
                            project_journal.os,
                            "open",
                            side_effect=replace_during_open,
                        ):
                            with mock.patch.object(
                                project_journal,
                                "_capture_bounded_process",
                            ) as capture:
                                project_journal._initialize_git_runtime()

                    self.assertTrue(replaced)
                    self.assertIsNone(project_journal._GIT_RUNTIME)
                    self.assertIsNotNone(project_journal._GIT_RUNTIME_ERROR)
                    self.assertIn(
                        "identity changed",
                        str(project_journal._GIT_RUNTIME_ERROR),
                    )
                    capture.assert_not_called()
        finally:
            runtime = project_journal._GIT_RUNTIME
            if runtime is not None and runtime is not old_runtime:
                runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

    def test_git_launch_executes_verified_copy_after_runtime_path_replacement(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-replacement")
        actual_popen = subprocess.Popen
        attacker = self.root / "attacker-git"
        attacker.write_text(
            "#!/bin/sh\nprintf 'attacker-executed\\n'\n",
            encoding="utf-8",
        )
        attacker.chmod(0o755)
        observed_launch: pathlib.Path | None = None

        def replace_runtime_before_popen(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            nonlocal observed_launch
            self.assertEqual(pathlib.Path(argv[0]), runtime.source_executable)
            observed_launch = pathlib.Path(str(kwargs["executable"]))
            self.assertNotEqual(observed_launch, runtime.executable)
            self.assertEqual(observed_launch.stat().st_mode & 0o777, 0o500)
            self.assertEqual(observed_launch.parent.stat().st_mode & 0o777, 0o500)
            os.replace(attacker, runtime.executable)
            return actual_popen(argv, *args, **kwargs)

        try:
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=replace_runtime_before_popen,
            ):
                result = project_journal._capture_bounded_process(
                    [str(runtime.executable), "probe"],
                    env={"PATH": os.environ.get("PATH", "")},
                    verified_runtime=runtime,
                    timeout_seconds=2,
                    stdout_limit=1024,
                    stderr_limit=1024,
                    stdout_overflow_error="stdout overflow",
                    stderr_overflow_error="stderr overflow",
                    timeout_error="launch timed out",
                    operation="replacement-bound Git launch",
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"safe-launch\n")
            self.assertIsNotNone(observed_launch)
            assert observed_launch is not None
            self.assertFalse(observed_launch.exists())
            self.assertEqual(
                runtime.executable.read_text(encoding="utf-8"),
                "#!/bin/sh\nprintf 'attacker-executed\\n'\n",
            )
        finally:
            runtime.snapshot_owner.cleanup()

    def test_git_launch_executes_verified_copy_after_runtime_path_becomes_fifo(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-fifo")
        actual_popen = subprocess.Popen
        observed_launch: pathlib.Path | None = None
        timeout_seconds = 2.0

        def replace_runtime_with_fifo_before_popen(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            nonlocal observed_launch
            self.assertEqual(pathlib.Path(argv[0]), runtime.source_executable)
            observed_launch = pathlib.Path(str(kwargs["executable"]))
            self.assertNotEqual(observed_launch, runtime.executable)
            runtime.executable.unlink()
            os.mkfifo(runtime.executable)
            return actual_popen(argv, *args, **kwargs)

        try:
            started = time.monotonic()
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=replace_runtime_with_fifo_before_popen,
            ):
                result = project_journal._capture_bounded_process(
                    [str(runtime.executable), "probe"],
                    env={"PATH": os.environ.get("PATH", "")},
                    verified_runtime=runtime,
                    timeout_seconds=timeout_seconds,
                    stdout_limit=1024,
                    stderr_limit=1024,
                    stdout_overflow_error="stdout overflow",
                    stderr_overflow_error="stderr overflow",
                    timeout_error="launch timed out",
                    operation="FIFO-bound Git launch",
                )

            self.assertLess(time.monotonic() - started, timeout_seconds)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"safe-launch\n")
            self.assertTrue(stat.S_ISFIFO(runtime.executable.stat().st_mode))
            self.assertIsNotNone(observed_launch)
            assert observed_launch is not None
            self.assertFalse(observed_launch.exists())
        finally:
            runtime.snapshot_owner.cleanup()

    @unittest.skipIf(
        os.geteuid() == 0,
        "UID 0 bypasses owner-directory DAC replacement denial",
    )
    def test_git_launch_directory_blocks_executable_replacement_before_popen(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-path-replacement")
        actual_popen = subprocess.Popen
        attacker = self.root / "launch-path-attacker"
        attacker.write_text(
            "#!/bin/sh\nprintf 'attacker-executed\\n'\n",
            encoding="utf-8",
        )
        attacker.chmod(0o755)

        def attempt_launch_replacement(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            launch_path = pathlib.Path(str(kwargs["executable"]))
            self.assertEqual(pathlib.Path(argv[0]), runtime.source_executable)
            self.assertEqual(launch_path.parent.stat().st_mode & 0o777, 0o500)
            with self.assertRaises(PermissionError):
                os.replace(attacker, launch_path)
            return actual_popen(argv, *args, **kwargs)

        try:
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=attempt_launch_replacement,
            ):
                result = project_journal._capture_bounded_process(
                    [str(runtime.executable), "probe"],
                    env={"PATH": os.environ.get("PATH", "")},
                    verified_runtime=runtime,
                    timeout_seconds=2,
                    stdout_limit=1024,
                    stderr_limit=1024,
                    stdout_overflow_error="stdout overflow",
                    stderr_overflow_error="stderr overflow",
                    timeout_error="launch timed out",
                    operation="replacement-resistant Git launch",
                )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"safe-launch\n")
            self.assertTrue(attacker.exists())
        finally:
            runtime.snapshot_owner.cleanup()

    @unittest.skipIf(
        os.geteuid() == 0,
        "UID 0 bypasses owner-directory DAC replacement denial",
    )
    def test_git_launch_directory_blocks_destination_replacement_before_reread(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-copy-replacement")
        actual_open = os.open
        attacker = self.root / "launch-copy-attacker"
        attacker.write_text(
            "#!/bin/sh\nprintf 'attacker-executed\\n'\n",
            encoding="utf-8",
        )
        attacker.chmod(0o755)
        replacement_attempted = False

        def attempt_launch_replacement_before_reread(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal replacement_attempted
            candidate = pathlib.Path(path)
            if (
                not replacement_attempted
                and candidate == pathlib.Path("git")
                and dir_fd is not None
                and (flags & os.O_ACCMODE) == os.O_RDONLY
            ):
                replacement_attempted = True
                self.assertTrue(flags & project_journal.os.O_NONBLOCK)
                self.assertTrue(flags & project_journal.os.O_NOFOLLOW)
                self.assertEqual(os.fstat(dir_fd).st_mode & 0o777, 0o500)
                with self.assertRaises(PermissionError):
                    os.replace(attacker, "git", dst_dir_fd=dir_fd)
            return actual_open(path, flags, mode, dir_fd=dir_fd)

        try:
            with mock.patch.object(
                project_journal.os,
                "open",
                side_effect=attempt_launch_replacement_before_reread,
            ):
                result = project_journal._capture_bounded_process(
                    [str(runtime.executable), "probe"],
                    env={"PATH": os.environ.get("PATH", "")},
                    verified_runtime=runtime,
                    timeout_seconds=2,
                    stdout_limit=1024,
                    stderr_limit=1024,
                    stdout_overflow_error="stdout overflow",
                    stderr_overflow_error="stderr overflow",
                    timeout_error="launch timed out",
                    operation="replacement-resistant Git reread",
                )

            self.assertTrue(replacement_attempted)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, b"safe-launch\n")
            self.assertTrue(attacker.exists())
        finally:
            runtime.snapshot_owner.cleanup()

    def test_git_launch_rejects_replacement_immediately_before_directory_lock(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-prelock-replacement")
        actual_fchmod = os.fchmod
        attacker = self.root / "launch-prelock-attacker"
        attacker.write_text(
            "#!/bin/sh\nprintf 'attacker-executed\\n'\n",
            encoding="utf-8",
        )
        attacker.chmod(0o500)
        replaced = False

        def replace_launch_before_directory_lock(
            fd: int,
            mode: int,
        ) -> None:
            nonlocal replaced
            if not replaced and mode == 0o500 and stat.S_ISDIR(os.fstat(fd).st_mode):
                os.replace(attacker, "git", dst_dir_fd=fd)
                replaced = True
            actual_fchmod(fd, mode)

        try:
            with mock.patch.object(
                project_journal.os,
                "fchmod",
                side_effect=replace_launch_before_directory_lock,
            ):
                with mock.patch.object(project_journal.subprocess, "Popen") as popen:
                    with self.assertRaisesRegex(
                        project_journal.UnsupportedGitVersion,
                        "launch identity changed",
                    ):
                        project_journal._capture_bounded_process(
                            [str(runtime.executable), "probe"],
                            env={"PATH": os.environ.get("PATH", "")},
                            verified_runtime=runtime,
                            timeout_seconds=2,
                            stdout_limit=1024,
                            stderr_limit=1024,
                            stdout_overflow_error="stdout overflow",
                            stderr_overflow_error="stderr overflow",
                            timeout_error="launch timed out",
                            operation="pre-lock tampered Git launch",
                        )

            self.assertTrue(replaced)
            popen.assert_not_called()
        finally:
            runtime.snapshot_owner.cleanup()

    def test_git_launch_rejects_unsticky_world_writable_temporary_root(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-unsafe-temporary-root")
        temporary_root = self.root / "unsafe-temporary-root"
        temporary_root.mkdir()
        temporary_root.chmod(0o777)

        try:
            with mock.patch.object(
                project_journal.tempfile,
                "gettempdir",
                return_value=str(temporary_root),
            ):
                with mock.patch.object(project_journal.subprocess, "Popen") as popen:
                    with self.assertRaisesRegex(
                        project_journal.UnsupportedGitVersion,
                        "group/world writable without the sticky bit",
                    ):
                        project_journal._capture_bounded_process(
                            [str(runtime.executable), "probe"],
                            env={"PATH": os.environ.get("PATH", "")},
                            verified_runtime=runtime,
                            timeout_seconds=2,
                            stdout_limit=1024,
                            stderr_limit=1024,
                            stdout_overflow_error="stdout overflow",
                            stderr_overflow_error="stderr overflow",
                            timeout_error="launch timed out",
                            operation="unsafe-root Git launch",
                        )

            popen.assert_not_called()
            self.assertEqual(list(temporary_root.iterdir()), [])
        finally:
            temporary_root.chmod(0o700)
            runtime.snapshot_owner.cleanup()

    def test_git_launch_rejects_foreign_owned_sticky_temporary_root(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-foreign-temporary-root")
        temporary_root = self.root / "foreign-temporary-root"
        temporary_root.mkdir()
        temporary_root.chmod(0o1777)
        actual_fstat = os.fstat
        root_identity = (
            temporary_root.stat().st_dev,
            temporary_root.stat().st_ino,
        )

        def foreign_root_owner(fd: int) -> os.stat_result:
            value = actual_fstat(fd)
            if (value.st_dev, value.st_ino) == root_identity:
                return stat_with_uid(value, os.geteuid() + 1)
            return value

        try:
            with mock.patch.object(
                project_journal.tempfile,
                "gettempdir",
                return_value=str(temporary_root),
            ):
                with mock.patch.object(
                    project_journal.os,
                    "fstat",
                    side_effect=foreign_root_owner,
                ):
                    with mock.patch.object(
                        project_journal.subprocess, "Popen"
                    ) as popen:
                        with self.assertRaisesRegex(
                            project_journal.UnsupportedGitVersion,
                            "not owned by root or the current user",
                        ):
                            project_journal._capture_bounded_process(
                                [str(runtime.executable), "probe"],
                                env={"PATH": os.environ.get("PATH", "")},
                                verified_runtime=runtime,
                                timeout_seconds=2,
                                stdout_limit=1024,
                                stderr_limit=1024,
                                stdout_overflow_error="stdout overflow",
                                stderr_overflow_error="stderr overflow",
                                timeout_error="launch timed out",
                                operation="foreign-root Git launch",
                            )

            popen.assert_not_called()
            self.assertEqual(list(temporary_root.iterdir()), [])
        finally:
            temporary_root.chmod(0o700)
            runtime.snapshot_owner.cleanup()

    def test_git_launch_rejects_foreign_owned_lexical_symlink_component(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-foreign-lexical-link")
        target = self.root / "lexical-target"
        temporary_root = target / "temporary-root"
        target.mkdir(mode=0o700)
        temporary_root.mkdir(mode=0o700)
        link = self.root / "lexical-link"
        link.symlink_to(target, target_is_directory=True)
        selected_root = link / temporary_root.name
        actual_stat = os.stat

        def foreign_link_owner(
            path: os.PathLike[str] | str | int,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            value = actual_stat(
                path,
                dir_fd=dir_fd,
                follow_symlinks=follow_symlinks,
            )
            if (
                dir_fd is None
                and not follow_symlinks
                and isinstance(path, (str, os.PathLike))
                and pathlib.Path(path) == link
            ):
                return stat_with_uid(value, os.geteuid() + 1)
            return value

        try:
            with mock.patch.object(
                project_journal.tempfile,
                "gettempdir",
                return_value=str(selected_root),
            ):
                with mock.patch.object(
                    project_journal.os,
                    "stat",
                    side_effect=foreign_link_owner,
                ):
                    with mock.patch.object(
                        project_journal.subprocess, "Popen"
                    ) as popen:
                        with self.assertRaisesRegex(
                            project_journal.UnsupportedGitVersion,
                            "lexical path entry is not owned by root or the current user",
                        ):
                            project_journal._capture_bounded_process(
                                [str(runtime.executable), "probe"],
                                env={"PATH": os.environ.get("PATH", "")},
                                verified_runtime=runtime,
                                timeout_seconds=2,
                                stdout_limit=1024,
                                stderr_limit=1024,
                                stdout_overflow_error="stdout overflow",
                                stderr_overflow_error="stderr overflow",
                                timeout_error="launch timed out",
                                operation="foreign-link Git launch",
                            )

            popen.assert_not_called()
            self.assertEqual(list(temporary_root.iterdir()), [])
        finally:
            runtime.snapshot_owner.cleanup()

    def test_git_launch_rejects_temporary_parent_replacement_before_exec(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-parent-replacement")
        temporary_root = self.root / "temporary-root"
        displaced_root = self.root / "displaced-temporary-root"
        temporary_root.mkdir(mode=0o700)
        actual_revalidate = project_journal._revalidate_git_launch_for_exec
        replaced = False

        def replace_parent_before_exec(
            launch: object,
            *,
            deadline: float,
            deadline_error: str,
        ) -> None:
            nonlocal replaced
            if not replaced:
                os.replace(temporary_root, displaced_root)
                temporary_root.mkdir(mode=0o700)
                replaced = True
            actual_revalidate(
                launch,
                deadline=deadline,
                deadline_error=deadline_error,
            )

        try:
            with mock.patch.object(
                project_journal.tempfile,
                "gettempdir",
                return_value=str(temporary_root),
            ):
                with mock.patch.object(
                    project_journal,
                    "_revalidate_git_launch_for_exec",
                    side_effect=replace_parent_before_exec,
                ):
                    with mock.patch.object(
                        project_journal.subprocess,
                        "Popen",
                    ) as popen:
                        with self.assertRaisesRegex(
                            project_journal.UserError,
                            "Git launch lexical path entry changed",
                        ) as raised:
                            project_journal._capture_bounded_process(
                                [str(runtime.executable), "probe"],
                                env={"PATH": os.environ.get("PATH", "")},
                                verified_runtime=runtime,
                                timeout_seconds=2,
                                stdout_limit=1024,
                                stderr_limit=1024,
                                stdout_overflow_error="stdout overflow",
                                stderr_overflow_error="stderr overflow",
                                timeout_error="launch timed out",
                                operation="parent-replaced Git launch",
                            )

            self.assertTrue(replaced)
            popen.assert_not_called()
            notes = "\n".join(getattr(raised.exception, "__notes__", ()))
            self.assertIn("launch-copy cleanup-incomplete", notes)
            self.assertIn("retained launch path is unverified", notes)
            self.assertIn("directory identity:", notes)
            self.assertNotIn("retained launch locator:", notes)
            self.assertTrue(any(displaced_root.glob("project-journal-git-launch-*")))
        finally:
            for root in (temporary_root, displaced_root):
                if root.exists():
                    for directory in root.glob("project-journal-git-launch-*"):
                        directory.chmod(0o700)
                        shutil.rmtree(directory)
                    root.chmod(0o700)
                    root.rmdir()
            runtime.snapshot_owner.cleanup()

    def test_git_launch_rejects_fifo_before_binding_without_starting_process(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-prebound-fifo")
        runtime.executable.unlink()
        os.mkfifo(runtime.executable)

        try:
            started = time.monotonic()
            with mock.patch.object(project_journal.subprocess, "Popen") as popen:
                with self.assertRaisesRegex(
                    project_journal.UnsupportedGitVersion,
                    "verified bytes",
                ):
                    project_journal._capture_bounded_process(
                        [str(runtime.executable), "probe"],
                        env={"PATH": os.environ.get("PATH", "")},
                        verified_runtime=runtime,
                        timeout_seconds=2,
                        stdout_limit=1024,
                        stderr_limit=1024,
                        stdout_overflow_error="stdout overflow",
                        stderr_overflow_error="stderr overflow",
                        timeout_error="launch timed out",
                        operation="pre-bound FIFO Git launch",
                    )

            self.assertLess(time.monotonic() - started, 1.0)
            popen.assert_not_called()
        finally:
            runtime.snapshot_owner.cleanup()

    def test_git_launch_copy_consumes_the_process_shared_deadline(self) -> None:
        runtime = self.make_fake_git_runtime("launch-shared-deadline")
        actual_read = os.read
        clock = {"now": 100.0}
        advanced = False

        def advance_during_launch_copy(fd: int, size: int) -> bytes:
            nonlocal advanced
            chunk = actual_read(fd, size)
            if chunk and not advanced:
                advanced = True
                clock["now"] = 103.0
            return chunk

        try:
            with mock.patch.object(
                project_journal.time,
                "monotonic",
                side_effect=lambda: clock["now"],
            ):
                with mock.patch.object(
                    project_journal.os,
                    "read",
                    side_effect=advance_during_launch_copy,
                ):
                    with mock.patch.object(
                        project_journal.subprocess, "Popen"
                    ) as popen:
                        with self.assertRaisesRegex(
                            project_journal.UserError,
                            "launch shared deadline",
                        ):
                            project_journal._capture_bounded_process(
                                [str(runtime.executable), "probe"],
                                env={"PATH": os.environ.get("PATH", "")},
                                verified_runtime=runtime,
                                timeout_seconds=2,
                                stdout_limit=1024,
                                stderr_limit=1024,
                                stdout_overflow_error="stdout overflow",
                                stderr_overflow_error="stderr overflow",
                                timeout_error="launch shared deadline",
                                operation="deadline-bound Git launch",
                            )

            self.assertTrue(advanced)
            popen.assert_not_called()
        finally:
            runtime.snapshot_owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_git_launch_is_retained_when_child_terminal_state_is_unverified(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-retained")
        actual_cleanup = project_journal._terminate_process_group_and_reap
        actual_popen = subprocess.Popen
        observed_launch: pathlib.Path | None = None

        def capture_launch(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            nonlocal observed_launch
            observed_launch = pathlib.Path(str(kwargs["executable"]))
            return actual_popen(argv, *args, **kwargs)

        def cleanup_then_report(
            process: subprocess.Popen[bytes],
            selector: object,
            ownership: project_journal._ProcessOwnership,
            known_returncode: int | None = None,
        ) -> str:
            actual_error = actual_cleanup(
                process,
                selector,
                ownership,
                known_returncode=known_returncode,
            )
            details = [
                detail
                for detail in (
                    actual_error,
                    "simulated unverified terminal state",
                )
                if detail
            ]
            return "; ".join(details)

        try:
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=capture_launch,
            ):
                with mock.patch.object(
                    project_journal,
                    "_terminate_process_group_and_reap",
                    side_effect=cleanup_then_report,
                ):
                    with self.assertRaises(project_journal.UserError) as raised:
                        project_journal._capture_bounded_process(
                            [str(runtime.executable), "probe"],
                            env={"PATH": os.environ.get("PATH", "")},
                            verified_runtime=runtime,
                            timeout_seconds=2,
                            stdout_limit=1024,
                            stderr_limit=1024,
                            stdout_overflow_error="stdout overflow",
                            stderr_overflow_error="stderr overflow",
                            timeout_error="launch timed out",
                            operation="retained Git launch",
                        )

            details = "\n".join(
                [
                    str(raised.exception),
                    *getattr(raised.exception, "__notes__", ()),
                ]
            )
            self.assertIn("simulated unverified terminal state", details)
            self.assertIn("retained launch locator", details)
            self.assertIsNotNone(observed_launch)
            assert observed_launch is not None
            self.assertIn(str(observed_launch.parent), details)
            self.assertTrue(observed_launch.exists())
            self.assertEqual(observed_launch.parent.stat().st_mode & 0o777, 0o500)
        finally:
            if observed_launch is not None and observed_launch.parent.exists():
                os.chmod(observed_launch.parent, 0o700)
                shutil.rmtree(observed_launch.parent)
            runtime.snapshot_owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_git_launch_post_start_revalidation_retains_on_incomplete_cleanup(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-post-start-revalidation")
        actual_revalidate = project_journal._revalidate_git_launch_for_exec
        actual_cleanup = project_journal._terminate_process_group_and_reap
        actual_popen = subprocess.Popen
        revalidation_count = 0
        observed_launch: pathlib.Path | None = None
        spawned: list[subprocess.Popen[bytes]] = []

        def fail_post_start_revalidation(
            launch: project_journal._GitLaunchCopy,
            *,
            deadline: float,
            deadline_error: str,
        ) -> None:
            nonlocal revalidation_count
            revalidation_count += 1
            if revalidation_count == 2:
                raise OSError("simulated post-start launch drift")
            actual_revalidate(
                launch,
                deadline=deadline,
                deadline_error=deadline_error,
            )

        def capture_launch(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            nonlocal observed_launch
            observed_launch = pathlib.Path(str(kwargs["executable"]))
            process = actual_popen(argv, *args, **kwargs)
            spawned.append(process)
            return process

        def cleanup_then_report(
            process: subprocess.Popen[bytes],
            selector: object,
            ownership: project_journal._ProcessOwnership,
            known_returncode: int | None = None,
        ) -> str:
            actual_error = actual_cleanup(
                process,
                selector,
                ownership,
                known_returncode=known_returncode,
            )
            details = [
                detail
                for detail in (
                    actual_error,
                    "simulated unverified post-start cleanup",
                )
                if detail
            ]
            return "; ".join(details)

        try:
            with mock.patch.object(
                project_journal,
                "_revalidate_git_launch_for_exec",
                side_effect=fail_post_start_revalidation,
            ):
                with mock.patch.object(
                    project_journal.subprocess,
                    "Popen",
                    side_effect=capture_launch,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_terminate_process_group_and_reap",
                        side_effect=cleanup_then_report,
                    ):
                        with self.assertRaises(project_journal.UserError) as raised:
                            project_journal._capture_bounded_process(
                                [str(runtime.executable), "probe"],
                                env={"PATH": os.environ.get("PATH", "")},
                                verified_runtime=runtime,
                                timeout_seconds=2,
                                stdout_limit=1024,
                                stderr_limit=1024,
                                stdout_overflow_error="stdout overflow",
                                stderr_overflow_error="stderr overflow",
                                timeout_error="launch timed out",
                                operation="post-start-drift Git launch",
                            )

            details = "\n".join(
                [
                    str(raised.exception),
                    *getattr(raised.exception, "__notes__", ()),
                ]
            )
            self.assertEqual(revalidation_count, 2)
            self.assertIn("post-start launch validation failed", details)
            self.assertNotIn("failed to start post-start-drift", details)
            self.assertIn("simulated unverified post-start cleanup", details)
            self.assertIn("retained launch locator", details)
            self.assertIsNotNone(observed_launch)
            assert observed_launch is not None
            self.assertTrue(observed_launch.exists())
        finally:
            for process in spawned:
                if process.poll() is None:
                    process.wait(timeout=5)
            if observed_launch is not None and observed_launch.parent.exists():
                os.chmod(observed_launch.parent, 0o700)
                shutil.rmtree(observed_launch.parent)
            runtime.snapshot_owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_git_launch_is_retained_after_process_identity_loss(self) -> None:
        runtime = self.make_fake_git_runtime("launch-identity-loss")
        actual_popen = subprocess.Popen
        spawned: list[subprocess.Popen[bytes]] = []
        observed_launch: pathlib.Path | None = None

        def capture_launch(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            nonlocal observed_launch
            observed_launch = pathlib.Path(str(kwargs["executable"]))
            process = actual_popen(argv, *args, **kwargs)
            spawned.append(process)
            return process

        try:
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=capture_launch,
            ):
                with mock.patch.object(
                    project_journal,
                    "_wait_for_process_status_without_reaping",
                    side_effect=project_journal._ProcessIdentityLost(
                        "simulated bound child identity loss"
                    ),
                ):
                    with self.assertRaises(project_journal.UserError) as raised:
                        project_journal._capture_bounded_process(
                            [str(runtime.executable), "probe"],
                            env={"PATH": os.environ.get("PATH", "")},
                            verified_runtime=runtime,
                            timeout_seconds=2,
                            stdout_limit=1024,
                            stderr_limit=1024,
                            stdout_overflow_error="stdout overflow",
                            stderr_overflow_error="stderr overflow",
                            timeout_error="launch timed out",
                            operation="identity-lost Git launch",
                        )

            details = "\n".join(
                [
                    str(raised.exception),
                    *getattr(raised.exception, "__notes__", ()),
                ]
            )
            self.assertIn("simulated bound child identity loss", details)
            self.assertIn("retained launch locator", details)
            self.assertIsNotNone(observed_launch)
            assert observed_launch is not None
            self.assertIn(str(observed_launch.parent), details)
            self.assertTrue(observed_launch.exists())
        finally:
            for process in spawned:
                process.wait(timeout=5)
            if observed_launch is not None and observed_launch.parent.exists():
                os.chmod(observed_launch.parent, 0o700)
                shutil.rmtree(observed_launch.parent)
            runtime.snapshot_owner.cleanup()

    def test_git_launch_cleanup_failure_preserves_original_error(self) -> None:
        runtime = self.make_fake_git_runtime("launch-cleanup-error")
        actual_cleanup = project_journal._GitLaunchCopy.cleanup
        original_error = LegacyUnsupportedPlatform(
            "original launch action failure",
        )
        original_args = original_error.args

        def reject_output(_chunk: bytes) -> None:
            raise original_error

        def cleanup_then_fail(
            launch: project_journal._GitLaunchCopy,
        ) -> None:
            actual_cleanup(launch)
            raise OSError("simulated launch cleanup failure")

        try:
            with mock.patch.object(
                project_journal._GitLaunchCopy,
                "cleanup",
                side_effect=cleanup_then_fail,
                autospec=True,
            ):
                try:
                    project_journal._capture_bounded_process(
                        [str(runtime.executable), "probe"],
                        env={"PATH": os.environ.get("PATH", "")},
                        verified_runtime=runtime,
                        timeout_seconds=2,
                        stdout_limit=1024,
                        stderr_limit=1024,
                        stdout_feed=reject_output,
                        stdout_overflow_error="stdout overflow",
                        stderr_overflow_error="stderr overflow",
                        timeout_error="launch timed out",
                        operation="cleanup-failing Git launch",
                    )
                except LegacyUnsupportedPlatform as exc:
                    raised_error = exc
                else:
                    self.fail("expected original launch action failure")

            self.assertIs(raised_error, original_error)
            self.assertIs(type(raised_error), LegacyUnsupportedPlatform)
            self.assertEqual(
                raised_error.code,
                project_journal.UnsupportedPlatform.code,
            )
            self.assertEqual(raised_error.args, original_args)
            notes = "\n".join(getattr(raised_error, "__notes__", ()))
            self.assertIn("launch-copy cleanup-incomplete", notes)
            self.assertIn("retained launch path is unverified", notes)
            self.assertIn("simulated launch cleanup failure", notes)
            traceback_names: list[str] = []
            traceback = raised_error.__traceback__
            while traceback is not None:
                traceback_names.append(traceback.tb_frame.f_code.co_name)
                traceback = traceback.tb_next
            self.assertIn("reject_output", traceback_names)
            self.assertNotIn("_report_git_launch_issue", traceback_names)
        finally:
            runtime.snapshot_owner.cleanup()

    def test_git_launch_cleanup_does_not_consume_ambient_exception(self) -> None:
        ambient = RuntimeError("unrelated outer exception")
        cleanup_error = project_journal.UserError(
            "injected ambient Git launch cleanup failure"
        )
        launch = mock.Mock(cleanup_safe=True)
        completed = subprocess.CompletedProcess(
            ["/bound/git", "probe"],
            0,
            b"ok\n",
            b"",
        )

        def cleanup_or_preserve(
            _launch: object,
            _operation: str,
            active_error: BaseException | None,
        ) -> None:
            if active_error is not None:
                project_journal._add_exception_detail(
                    active_error,
                    str(cleanup_error),
                )
                return
            raise cleanup_error

        try:
            raise ambient
        except RuntimeError:
            with (
                mock.patch.object(
                    project_journal,
                    "_prepare_git_runtime_launch",
                    return_value=launch,
                ),
                mock.patch.object(
                    project_journal,
                    "_capture_bounded_process_with_launch",
                    return_value=completed,
                ),
                mock.patch.object(
                    project_journal,
                    "_cleanup_git_launch_after_terminal",
                    side_effect=cleanup_or_preserve,
                ),
                self.assertRaises(project_journal.UserError) as raised,
            ):
                project_journal._capture_bounded_process(
                    ["/bound/git", "probe"],
                    env={},
                    verified_runtime=mock.sentinel.runtime,
                    timeout_seconds=2,
                    stdout_limit=1024,
                    stderr_limit=1024,
                    stdout_overflow_error="stdout overflow",
                    stderr_overflow_error="stderr overflow",
                    timeout_error="launch timed out",
                    operation="ambient Git launch",
                )

        self.assertIs(raised.exception, cleanup_error)
        self.assertEqual(getattr(ambient, "__notes__", ()), ())

    @unittest.skipUnless(os.name == "posix", "POSIX process-status contract")
    def test_process_observer_close_does_not_consume_ambient_exception(
        self,
    ) -> None:
        ambient = RuntimeError("unrelated outer exception")
        observer_error = "injected ambient process observer close failure"

        try:
            raise ambient
        except RuntimeError:
            with (
                mock.patch.object(
                    project_journal,
                    "_close_process_status_observer",
                    return_value=observer_error,
                ),
                self.assertRaisesRegex(
                    project_journal.UserError,
                    observer_error,
                ),
            ):
                self.capture_process(
                    [sys.executable, "-c", "pass"],
                    timeout_seconds=2,
                    stdout_limit=1024,
                )

        self.assertEqual(getattr(ambient, "__notes__", ()), ())

    @unittest.skipUnless(os.name == "posix", "POSIX process-status contract")
    def test_identity_settlement_failure_receives_observer_cleanup_evidence(
        self,
    ) -> None:
        settlement_error = LegacyInterrupt("injected identity-settlement interruption")
        observer_error = "injected observer close failure after settlement"
        actual_popen = project_journal.subprocess.Popen
        spawned: list[subprocess.Popen[bytes]] = []

        def capture_process(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            process = actual_popen(argv, *args, **kwargs)
            spawned.append(process)
            return process

        try:
            with (
                mock.patch.object(
                    project_journal.subprocess,
                    "Popen",
                    side_effect=capture_process,
                ),
                mock.patch.object(
                    project_journal,
                    "_wait_for_process_status_without_reaping",
                    side_effect=project_journal._ProcessIdentityLost(
                        "injected process identity loss"
                    ),
                ),
                mock.patch.object(
                    project_journal,
                    "_settle_direct_child_after_identity_loss",
                    side_effect=settlement_error,
                ),
                mock.patch.object(
                    project_journal,
                    "_close_process_status_observer",
                    return_value=observer_error,
                ),
                self.assertRaises(LegacyInterrupt) as raised,
            ):
                self.capture_process(
                    [sys.executable, "-c", "pass"],
                    timeout_seconds=2,
                    stdout_limit=1024,
                )
        finally:
            for process in spawned:
                process.wait(timeout=5)

        self.assertIs(raised.exception, settlement_error)
        self.assertIn(
            observer_error,
            "\n".join(getattr(settlement_error, "__notes__", ())),
        )

    @unittest.skipUnless(os.name == "posix", "POSIX process-status contract")
    def test_generic_cleanup_handler_failure_receives_observer_evidence(
        self,
    ) -> None:
        primary = LegacyInterrupt("injected process action interruption")
        handler_error = LegacyInterrupt("injected stream cleanup interruption")
        observer_error = "injected observer close failure after handler"

        def reject_stdout(_chunk: bytes) -> None:
            raise primary

        with (
            mock.patch.object(
                project_journal,
                "_close_process_streams",
                side_effect=handler_error,
            ),
            mock.patch.object(
                project_journal,
                "_close_process_status_observer",
                return_value=observer_error,
            ),
            self.assertRaises(LegacyInterrupt) as raised,
        ):
            self.capture_process(
                [sys.executable, "-c", "print('output')"],
                timeout_seconds=2,
                stdout_limit=1024,
                stdout_feed=reject_stdout,
            )

        self.assertIs(raised.exception, handler_error)
        self.assertIn(
            observer_error,
            "\n".join(getattr(handler_error, "__notes__", ())),
        )
        self.assertEqual(getattr(primary, "__notes__", ()), ())

    @unittest.skipUnless(os.name == "posix", "POSIX process-status contract")
    def test_observer_cleanup_baseexception_does_not_consume_ambient_exception(
        self,
    ) -> None:
        ambient = RuntimeError("unrelated outer exception")
        observer_error = LegacyInterrupt(
            "injected process observer cleanup interruption"
        )

        try:
            raise ambient
        except RuntimeError:
            with (
                mock.patch.object(
                    project_journal,
                    "_close_process_status_observer",
                    side_effect=observer_error,
                ),
                self.assertRaises(LegacyInterrupt) as raised,
            ):
                self.capture_process(
                    [sys.executable, "-c", "pass"],
                    timeout_seconds=2,
                    stdout_limit=1024,
                )

        self.assertIs(raised.exception, observer_error)
        self.assertEqual(getattr(ambient, "__notes__", ()), ())

    @unittest.skipUnless(os.name == "posix", "POSIX process-status contract")
    def test_observer_cleanup_baseexception_preserves_process_primary(
        self,
    ) -> None:
        primary = LegacyInterrupt("injected process primary interruption")
        observer_error = OSError(
            errno.EIO,
            "injected process observer cleanup failure",
        )

        def reject_stdout(_chunk: bytes) -> None:
            raise primary

        with (
            mock.patch.object(
                project_journal,
                "_close_process_status_observer",
                side_effect=observer_error,
            ),
            self.assertRaises(LegacyInterrupt) as raised,
        ):
            self.capture_process(
                [sys.executable, "-c", "print('output')"],
                timeout_seconds=2,
                stdout_limit=1024,
                stdout_feed=reject_stdout,
            )

        self.assertIs(raised.exception, primary)
        notes = "\n".join(getattr(primary, "__notes__", ()))
        self.assertIn("process-status observer cleanup failed", notes)
        self.assertIn("type=OSError", notes)
        self.assertIn("errno=5 (EIO)", notes)
        self.assertIn(str(observer_error), notes)

    def test_git_launch_descriptor_close_preserves_active_primary(self) -> None:
        primary = LegacyInterrupt("simulated launch validation primary")
        close_error = OSError(errno.EIO, "simulated launch descriptor close failure")

        with mock.patch.object(
            project_journal.os,
            "close",
            side_effect=close_error,
        ):
            try:
                try:
                    raise primary
                finally:
                    project_journal._close_git_launch_descriptor(
                        8123,
                        sys.exc_info()[1],
                        context="injected Git launch revalidation",
                    )
            except LegacyInterrupt as raised:
                observed = raised
            else:
                self.fail("expected the active launch validation primary")

        self.assertIs(observed, primary)
        details = "\n".join(getattr(observed, "__notes__", ()))
        self.assertIn("Git launch revalidation descriptor cleanup failed", details)
        self.assertIn("simulated launch descriptor close failure", details)

    def test_git_launch_descriptor_close_only_baseexception_remains_exact(
        self,
    ) -> None:
        close_error = LegacyInterrupt(
            "simulated close-only launch descriptor interruption"
        )

        with mock.patch.object(
            project_journal.os,
            "close",
            side_effect=close_error,
        ):
            try:
                project_journal._close_git_launch_descriptor(
                    8124,
                    None,
                    context="injected Git launch revalidation",
                )
            except LegacyInterrupt as raised:
                observed = raised
            else:
                self.fail("expected the close-only descriptor interruption")

        self.assertIs(observed, close_error)
        details = "\n".join(getattr(observed, "__notes__", ()))
        self.assertIn("Git launch revalidation descriptor cleanup failed", details)

    def test_git_launch_directory_close_only_baseexception_remains_exact(
        self,
    ) -> None:
        close_error = LegacyInterrupt(
            "simulated bound launch directory close interruption"
        )
        ancestor = project_journal._BoundGitLaunchDirectory(
            path=self.root,
            fd=8125,
            identity=(1, 2, os.geteuid(), os.getegid(), 0o700),
            parent_fd=None,
            component=None,
            owner_private=True,
        )

        with mock.patch.object(
            project_journal.os,
            "close",
            side_effect=close_error,
        ):
            try:
                project_journal._close_bound_git_launch_directories(
                    (ancestor,),
                    None,
                )
            except LegacyInterrupt as raised:
                observed = raised
            else:
                self.fail("expected the bound-directory close interruption")

        self.assertIs(observed, close_error)
        self.assertEqual(ancestor.fd, -1)
        details = "\n".join(getattr(observed, "__notes__", ()))
        self.assertIn("Git launch path descriptor cleanup 1 failed", details)

    def test_git_snapshot_binding_close_failure_preserves_active_error(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("snapshot-binding-close-error")
        actual_close = project_journal.os.close
        source_fd: int | None = None
        source_close_failed = False
        original_error = LegacyInterrupt(
            "simulated Git snapshot binding interruption",
        )
        original_args = original_error.args

        def interrupt_binding(
            runtime_value: project_journal._GitRuntime,
            fd: int,
            *,
            deadline: float | None = None,
            deadline_error: str,
        ) -> None:
            nonlocal source_fd
            del runtime_value, deadline, deadline_error
            source_fd = fd
            raise original_error

        def close_source_then_fail(fd: int) -> None:
            nonlocal source_close_failed
            if fd == source_fd and not source_close_failed:
                source_close_failed = True
                actual_close(fd)
                raise OSError(
                    errno.EIO,
                    "simulated snapshot binding close failure",
                )
            actual_close(fd)

        try:
            with mock.patch.object(
                project_journal,
                "_revalidate_open_git_runtime_snapshot",
                side_effect=interrupt_binding,
            ):
                with mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=close_source_then_fail,
                ):
                    try:
                        project_journal._open_bound_git_runtime_snapshot(
                            runtime,
                            deadline=time.monotonic() + 5,
                            deadline_error="snapshot binding timed out",
                        )
                    except LegacyInterrupt as exc:
                        raised_error = exc
                    else:
                        self.fail("expected Git snapshot binding interruption")

            self.assertIs(raised_error, original_error)
            self.assertEqual(raised_error.args, original_args)
            self.assertTrue(source_close_failed)
            notes = "\n".join(getattr(raised_error, "__notes__", ()))
            self.assertIn("source descriptor cleanup failed", notes)
            self.assertIn("snapshot binding close failure", notes)
            traceback_names = self.exception_traceback_names(raised_error)
            self.assertIn("interrupt_binding", traceback_names)
            self.assertNotIn(
                "_close_git_runtime_snapshot_descriptor_preserving_error",
                traceback_names,
            )
        finally:
            runtime.snapshot_owner.cleanup()

    def test_git_snapshot_verification_close_failure_preserves_active_error(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("snapshot-verification-close-error")
        actual_open = project_journal._open_bound_git_runtime_snapshot
        actual_close = project_journal.os.close
        source_fd: int | None = None
        source_close_failed = False
        original_error = LegacyInterrupt(
            "simulated Git snapshot verification interruption",
        )
        original_args = original_error.args

        def capture_source(
            runtime_value: project_journal._GitRuntime,
            *,
            deadline: float | None = None,
            deadline_error: str,
        ) -> int:
            nonlocal source_fd
            source_fd = actual_open(
                runtime_value,
                deadline=deadline,
                deadline_error=deadline_error,
            )
            return source_fd

        def interrupt_hash(
            fd: int,
            *,
            deadline: float | None,
            deadline_error: str,
        ) -> tuple[str, int]:
            del deadline, deadline_error
            self.assertEqual(fd, source_fd)
            raise original_error

        def close_source_then_fail(fd: int) -> None:
            nonlocal source_close_failed
            if fd == source_fd and not source_close_failed:
                source_close_failed = True
                actual_close(fd)
                raise OSError(
                    errno.EIO,
                    "simulated snapshot verification close failure",
                )
            actual_close(fd)

        try:
            with mock.patch.object(
                project_journal,
                "_open_bound_git_runtime_snapshot",
                side_effect=capture_source,
            ):
                with mock.patch.object(
                    project_journal,
                    "_hash_open_file",
                    side_effect=interrupt_hash,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "close",
                        side_effect=close_source_then_fail,
                    ):
                        try:
                            project_journal._verify_git_runtime_snapshot(
                                runtime,
                                deadline=time.monotonic() + 5,
                                deadline_error="snapshot verification timed out",
                            )
                        except LegacyInterrupt as exc:
                            raised_error = exc
                        else:
                            self.fail("expected Git snapshot verification interruption")

            self.assertIs(raised_error, original_error)
            self.assertEqual(raised_error.args, original_args)
            self.assertTrue(source_close_failed)
            notes = "\n".join(getattr(raised_error, "__notes__", ()))
            self.assertIn("source descriptor cleanup failed", notes)
            self.assertIn("snapshot verification close failure", notes)
            traceback_names = self.exception_traceback_names(raised_error)
            self.assertIn("interrupt_hash", traceback_names)
            self.assertNotIn(
                "_close_git_runtime_snapshot_descriptor_preserving_error",
                traceback_names,
            )
        finally:
            runtime.snapshot_owner.cleanup()

    def test_git_launch_source_close_failure_cleans_prepared_launch(self) -> None:
        runtime = self.make_fake_git_runtime("launch-source-close-error")
        actual_open_source = project_journal._open_bound_git_runtime_snapshot
        actual_close = project_journal.os.close
        actual_mkdtemp = tempfile.mkdtemp
        source_fd: int | None = None
        source_close_failed = False
        launch_directories: list[pathlib.Path] = []
        close_error = OSError(
            errno.EIO,
            "simulated Git launch source close failure",
        )

        def capture_source(*args: object, **kwargs: object) -> int:
            nonlocal source_fd
            source_fd = actual_open_source(*args, **kwargs)
            return source_fd

        def close_source_then_fail(fd: int) -> None:
            nonlocal source_close_failed
            if fd == source_fd and not source_close_failed:
                source_close_failed = True
                actual_close(fd)
                raise close_error
            actual_close(fd)

        def capture_launch_directory(*args: object, **kwargs: object) -> str:
            directory = actual_mkdtemp(*args, **kwargs)
            if pathlib.Path(directory).name.startswith("project-journal-git-launch-"):
                launch_directories.append(pathlib.Path(directory))
            return directory

        try:
            with mock.patch.object(
                project_journal,
                "_open_bound_git_runtime_snapshot",
                side_effect=capture_source,
            ):
                with mock.patch.object(
                    project_journal.tempfile,
                    "mkdtemp",
                    side_effect=capture_launch_directory,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "close",
                        side_effect=close_source_then_fail,
                    ):
                        with self.assertRaises(
                            project_journal.UnsupportedGitVersion,
                        ) as raised:
                            project_journal._prepare_git_runtime_launch(
                                runtime,
                                deadline=time.monotonic() + 5,
                                deadline_error="launch preparation timed out",
                            )

            self.assertTrue(source_close_failed)
            self.assertIs(raised.exception.__cause__, close_error)
            self.assertIn(
                "source descriptor cleanup failed",
                str(raised.exception),
            )
            self.assertEqual(len(launch_directories), 1)
            self.assertFalse(launch_directories[0].exists())
        finally:
            for directory in launch_directories:
                if directory.exists():
                    os.chmod(directory, 0o700)
                    shutil.rmtree(directory)
            runtime.snapshot_owner.cleanup()

    def test_git_launch_source_close_failure_is_secondary_to_active_error(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-source-close-secondary")
        actual_open_source = project_journal._open_bound_git_runtime_snapshot
        actual_close = project_journal.os.close
        actual_mkdtemp = tempfile.mkdtemp
        actual_fchmod = project_journal.os.fchmod
        source_fd: int | None = None
        source_close_failed = False
        interrupted = False
        launch_directories: list[pathlib.Path] = []
        original_error = LegacyInterrupt(
            "simulated launch preparation interruption",
        )
        original_args = original_error.args

        def capture_source(*args: object, **kwargs: object) -> int:
            nonlocal source_fd
            source_fd = actual_open_source(*args, **kwargs)
            return source_fd

        def close_source_then_fail(fd: int) -> None:
            nonlocal source_close_failed
            if fd == source_fd and not source_close_failed:
                source_close_failed = True
                actual_close(fd)
                raise OSError(
                    errno.EIO,
                    "simulated secondary source close failure",
                )
            actual_close(fd)

        def capture_launch_directory(*args: object, **kwargs: object) -> str:
            directory = actual_mkdtemp(*args, **kwargs)
            if pathlib.Path(directory).name.startswith("project-journal-git-launch-"):
                launch_directories.append(pathlib.Path(directory))
            return directory

        def interrupt_initial_launch_chmod(
            fd: int,
            mode: int,
        ) -> None:
            nonlocal interrupted
            if not interrupted and mode == 0o700 and stat.S_ISDIR(os.fstat(fd).st_mode):
                interrupted = True
                raise original_error
            actual_fchmod(fd, mode)

        try:
            with mock.patch.object(
                project_journal,
                "_open_bound_git_runtime_snapshot",
                side_effect=capture_source,
            ):
                with mock.patch.object(
                    project_journal.tempfile,
                    "mkdtemp",
                    side_effect=capture_launch_directory,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "fchmod",
                        side_effect=interrupt_initial_launch_chmod,
                    ):
                        with mock.patch.object(
                            project_journal.os,
                            "close",
                            side_effect=close_source_then_fail,
                        ):
                            try:
                                project_journal._prepare_git_runtime_launch(
                                    runtime,
                                    deadline=time.monotonic() + 5,
                                    deadline_error="launch preparation timed out",
                                )
                            except LegacyInterrupt as exc:
                                raised_error = exc
                            else:
                                self.fail("expected launch preparation interruption")

            self.assertIs(raised_error, original_error)
            self.assertEqual(raised_error.args, original_args)
            self.assertTrue(interrupted)
            self.assertTrue(source_close_failed)
            notes = "\n".join(getattr(raised_error, "__notes__", ()))
            self.assertIn("source descriptor cleanup failed", notes)
            self.assertIn("secondary source close failure", notes)
            self.assertEqual(len(launch_directories), 1)
            self.assertFalse(launch_directories[0].exists())
            traceback_names = self.exception_traceback_names(raised_error)
            self.assertIn("interrupt_initial_launch_chmod", traceback_names)
            self.assertNotIn("close_source", traceback_names)
        finally:
            for directory in launch_directories:
                if directory.exists():
                    os.chmod(directory, 0o700)
                    shutil.rmtree(directory)
            runtime.snapshot_owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_git_launch_source_close_preserves_close_error_over_pending_sigint(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-close-error-pending-sigint")
        actual_open_source = project_journal._open_bound_git_runtime_snapshot
        actual_close = project_journal.os.close
        actual_mkdtemp = tempfile.mkdtemp
        source_fd: int | None = None
        source_close_count = 0
        launch_directories: list[pathlib.Path] = []
        close_error = OSError(
            errno.EIO,
            "injected source close failure before pending SIGINT restore",
        )

        def capture_source(*args: object, **kwargs: object) -> int:
            nonlocal source_fd
            source_fd = actual_open_source(*args, **kwargs)
            return source_fd

        def close_then_fail_with_pending_sigint(fd: int) -> None:
            nonlocal source_close_count
            if fd == source_fd and source_close_count == 0:
                source_close_count += 1
                actual_close(fd)
                os.kill(os.getpid(), signal.SIGINT)
                raise close_error
            actual_close(fd)

        def capture_launch_directory(*args: object, **kwargs: object) -> str:
            directory = actual_mkdtemp(*args, **kwargs)
            candidate = pathlib.Path(directory)
            if candidate.name.startswith("project-journal-git-launch-"):
                launch_directories.append(candidate)
            return directory

        try:
            with self.default_unblocked_sigint():
                with mock.patch.object(
                    project_journal,
                    "_open_bound_git_runtime_snapshot",
                    side_effect=capture_source,
                ):
                    with mock.patch.object(
                        project_journal.tempfile,
                        "mkdtemp",
                        side_effect=capture_launch_directory,
                    ):
                        with mock.patch.object(
                            project_journal.os,
                            "close",
                            side_effect=close_then_fail_with_pending_sigint,
                        ):
                            with self.assertRaises(
                                project_journal.UnsupportedGitVersion,
                            ) as raised:
                                project_journal._prepare_git_runtime_launch(
                                    runtime,
                                    deadline=time.monotonic() + 5,
                                    deadline_error="launch preparation timed out",
                                )

            self.assertIs(raised.exception.__cause__, close_error)
            self.assertEqual(source_close_count, 1)
            self.assertIsNotNone(source_fd)
            assert source_fd is not None
            with self.assertRaises(OSError) as closed:
                os.fstat(source_fd)
            self.assertEqual(closed.exception.errno, errno.EBADF)
            notes = "\n".join(getattr(raised.exception, "__notes__", ()))
            self.assertIn("type=KeyboardInterrupt", notes)
            self.assertEqual(notes.count("type=KeyboardInterrupt"), 1)
            self.assertEqual(len(launch_directories), 1)
            self.assertFalse(launch_directories[0].exists())
        finally:
            for directory in launch_directories:
                if directory.exists():
                    os.chmod(directory, 0o700)
                    shutil.rmtree(directory)
            runtime.snapshot_owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_git_launch_source_dual_close_restore_failure_is_secondary_to_active_error(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime(
            "launch-active-error-close-error-pending-sigint"
        )
        actual_open_source = project_journal._open_bound_git_runtime_snapshot
        actual_close = project_journal.os.close
        actual_mkdtemp = tempfile.mkdtemp
        actual_fchmod = project_journal.os.fchmod
        source_fd: int | None = None
        source_close_count = 0
        launch_directories: list[pathlib.Path] = []
        active_error = LegacyInterrupt(
            "injected active launch preparation interruption"
        )
        close_error = OSError(
            errno.EIO,
            "injected secondary source close failure",
        )
        interrupted = False

        def capture_source(*args: object, **kwargs: object) -> int:
            nonlocal source_fd
            source_fd = actual_open_source(*args, **kwargs)
            return source_fd

        def close_then_fail_with_pending_sigint(fd: int) -> None:
            nonlocal source_close_count
            if fd == source_fd and source_close_count == 0:
                source_close_count += 1
                actual_close(fd)
                os.kill(os.getpid(), signal.SIGINT)
                raise close_error
            actual_close(fd)

        def capture_launch_directory(*args: object, **kwargs: object) -> str:
            directory = actual_mkdtemp(*args, **kwargs)
            candidate = pathlib.Path(directory)
            if candidate.name.startswith("project-journal-git-launch-"):
                launch_directories.append(candidate)
            return directory

        def interrupt_initial_launch_chmod(
            fd: int,
            mode: int,
        ) -> None:
            nonlocal interrupted
            if not interrupted and mode == 0o700 and stat.S_ISDIR(os.fstat(fd).st_mode):
                interrupted = True
                raise active_error
            actual_fchmod(fd, mode)

        try:
            with self.default_unblocked_sigint():
                with mock.patch.object(
                    project_journal,
                    "_open_bound_git_runtime_snapshot",
                    side_effect=capture_source,
                ):
                    with mock.patch.object(
                        project_journal.tempfile,
                        "mkdtemp",
                        side_effect=capture_launch_directory,
                    ):
                        with mock.patch.object(
                            project_journal.os,
                            "fchmod",
                            side_effect=interrupt_initial_launch_chmod,
                        ):
                            with mock.patch.object(
                                project_journal.os,
                                "close",
                                side_effect=close_then_fail_with_pending_sigint,
                            ):
                                with self.assertRaises(LegacyInterrupt) as raised:
                                    project_journal._prepare_git_runtime_launch(
                                        runtime,
                                        deadline=time.monotonic() + 5,
                                        deadline_error=("launch preparation timed out"),
                                    )

            self.assertIs(raised.exception, active_error)
            self.assertTrue(interrupted)
            self.assertEqual(source_close_count, 1)
            cleanup_errors = getattr(active_error, "cleanup_errors", ())
            self.assertEqual(len(cleanup_errors), 1)
            cleanup = cleanup_errors[0]
            self.assertEqual(cleanup["error_type"], "OSError")
            self.assertEqual(cleanup["errno"], errno.EIO)
            self.assertEqual(cleanup["error_name"], "EIO")
            self.assertIn(
                "type=KeyboardInterrupt",
                "\n".join(cleanup["details"]),
            )
            notes = "\n".join(getattr(active_error, "__notes__", ()))
            self.assertIn("secondary source close failure", notes)
            self.assertIn("type=KeyboardInterrupt", notes)
            self.assertEqual(len(launch_directories), 1)
            self.assertFalse(launch_directories[0].exists())
        finally:
            for directory in launch_directories:
                if directory.exists():
                    os.chmod(directory, 0o700)
                    shutil.rmtree(directory)
            runtime.snapshot_owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_git_launch_source_close_signal_fence_covers_handoff_phases(
        self,
    ) -> None:
        prepare = project_journal._prepare_git_runtime_launch
        target_lines = {
            "before_signal_fence": self.exact_source_line(
                prepare,
                "signal_fence = _block_fd_close_signals()",
            ),
            "after_close_commit": self.exact_source_line(
                prepare,
                "os.close(fd)",
            ),
            "after_close_success": self.exact_source_line(
                prepare,
                "if close_committed:",
            ),
        }
        actual_open_source = project_journal._open_bound_git_runtime_snapshot
        actual_close = project_journal.os.close
        actual_mkdtemp = tempfile.mkdtemp

        for phase, target_line in target_lines.items():
            with self.subTest(phase=phase):
                runtime = self.make_fake_git_runtime(f"launch-sigint-{phase}")
                source_identity: tuple[int, int] | None = None
                source_close_count = 0
                launch_directories: list[pathlib.Path] = []
                injected = False

                def capture_source(*args: object, **kwargs: object) -> int:
                    nonlocal source_identity
                    fd = actual_open_source(*args, **kwargs)
                    source_stat = os.fstat(fd)
                    source_identity = (source_stat.st_dev, source_stat.st_ino)
                    return fd

                def track_close(fd: int) -> None:
                    nonlocal source_close_count
                    try:
                        descriptor_stat = os.fstat(fd)
                    except OSError:
                        descriptor_identity = None
                    else:
                        descriptor_identity = (
                            descriptor_stat.st_dev,
                            descriptor_stat.st_ino,
                        )
                    if descriptor_identity == source_identity:
                        source_close_count += 1
                    actual_close(fd)

                def capture_launch_directory(
                    *args: object,
                    **kwargs: object,
                ) -> str:
                    directory = actual_mkdtemp(*args, **kwargs)
                    candidate = pathlib.Path(directory)
                    if candidate.name.startswith("project-journal-git-launch-"):
                        launch_directories.append(candidate)
                    return directory

                def send_sigint_at_target(
                    frame: object,
                    event: str,
                    _arg: object,
                ) -> object:
                    nonlocal injected
                    code = getattr(frame, "f_code", None)
                    if (
                        not injected
                        and event == "line"
                        and getattr(code, "co_name", None) == "attempt_close"
                        and getattr(code, "co_filename", None) == str(SCRIPT)
                        and getattr(frame, "f_lineno", None) == target_line
                    ):
                        injected = True
                        os.kill(os.getpid(), signal.SIGINT)
                    return send_sigint_at_target

                previous_trace = sys.gettrace()
                try:
                    with self.default_unblocked_sigint():
                        with mock.patch.object(
                            project_journal,
                            "_open_bound_git_runtime_snapshot",
                            side_effect=capture_source,
                        ):
                            with mock.patch.object(
                                project_journal.tempfile,
                                "mkdtemp",
                                side_effect=capture_launch_directory,
                            ):
                                with mock.patch.object(
                                    project_journal.os,
                                    "close",
                                    side_effect=track_close,
                                ):
                                    sys.settrace(send_sigint_at_target)
                                    with self.assertRaises(KeyboardInterrupt):
                                        prepare(
                                            runtime,
                                            deadline=time.monotonic() + 5,
                                            deadline_error=(
                                                "launch preparation timed out"
                                            ),
                                        )
                finally:
                    sys.settrace(previous_trace)
                    for directory in launch_directories:
                        if directory.exists():
                            os.chmod(directory, 0o700)
                            shutil.rmtree(directory)
                    runtime.snapshot_owner.cleanup()

                self.assertTrue(injected)
                self.assertIsNotNone(source_identity)
                self.assertEqual(source_close_count, 1)
                self.assertTrue(launch_directories)
                self.assertTrue(
                    all(not directory.exists() for directory in launch_directories)
                )

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_git_launch_source_close_signal_fence_does_not_close_reused_fd(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-sigint-fd-reuse")
        actual_open_source = project_journal._open_bound_git_runtime_snapshot
        actual_close = project_journal.os.close
        source_identity: tuple[int, int] | None = None
        source_closed = False
        reused_fd: int | None = None

        def capture_source(*args: object, **kwargs: object) -> int:
            nonlocal source_identity
            fd = actual_open_source(*args, **kwargs)
            source_stat = os.fstat(fd)
            source_identity = (source_stat.st_dev, source_stat.st_ino)
            return fd

        def close_source_then_reuse(fd: int) -> None:
            nonlocal reused_fd, source_closed
            descriptor_stat = os.fstat(fd)
            descriptor_identity = (
                descriptor_stat.st_dev,
                descriptor_stat.st_ino,
            )
            if descriptor_identity == source_identity and not source_closed:
                source_closed = True
                actual_close(fd)
                replacement = os.open(os.devnull, os.O_RDONLY)
                if replacement != fd:
                    os.dup2(replacement, fd)
                    actual_close(replacement)
                reused_fd = fd
                os.kill(os.getpid(), signal.SIGINT)
                return
            actual_close(fd)

        try:
            with self.default_unblocked_sigint():
                with mock.patch.object(
                    project_journal,
                    "_open_bound_git_runtime_snapshot",
                    side_effect=capture_source,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "close",
                        side_effect=close_source_then_reuse,
                    ):
                        with self.assertRaises(KeyboardInterrupt):
                            project_journal._prepare_git_runtime_launch(
                                runtime,
                                deadline=time.monotonic() + 5,
                                deadline_error="launch preparation timed out",
                            )

            self.assertTrue(source_closed)
            self.assertIsNotNone(reused_fd)
            assert reused_fd is not None
            os.fstat(reused_fd)
        finally:
            if reused_fd is not None:
                try:
                    actual_close(reused_fd)
                except OSError:
                    pass
            runtime.snapshot_owner.cleanup()

    def test_git_launch_source_close_reports_incomplete_preclose_drain(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-preclose-drain-failure")
        actual_open_source = project_journal._open_bound_git_runtime_snapshot
        actual_close = project_journal.os.close
        source_fd: int | None = None
        first_interrupt = LegacyInterrupt("injected pre-close interruption")
        drain_interrupt = LegacyInterrupt("injected pre-close drain interruption")

        def capture_source(*args: object, **kwargs: object) -> int:
            nonlocal source_fd
            source_fd = actual_open_source(*args, **kwargs)
            return source_fd

        try:
            with mock.patch.object(
                project_journal,
                "_open_bound_git_runtime_snapshot",
                side_effect=capture_source,
            ):
                with mock.patch.object(
                    project_journal,
                    "_block_fd_close_signals",
                    side_effect=[first_interrupt, drain_interrupt],
                ):
                    with self.assertRaises(LegacyInterrupt) as raised:
                        project_journal._prepare_git_runtime_launch(
                            runtime,
                            deadline=time.monotonic() + 5,
                            deadline_error="launch preparation timed out",
                        )

            self.assertIs(raised.exception, first_interrupt)
            details = "\n".join(
                [
                    str(raised.exception),
                    *getattr(raised.exception, "__notes__", ()),
                ]
            )
            self.assertIn("source descriptor owner drain failed", details)
            self.assertIn(str(drain_interrupt), details)
            self.assertIn("source descriptor remains owned", details)
            self.assertIsNotNone(source_fd)
            assert source_fd is not None
            os.fstat(source_fd)
        finally:
            if source_fd is not None:
                try:
                    actual_close(source_fd)
                except OSError:
                    pass
            runtime.snapshot_owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX Git runtime contract")
    def test_git_snapshot_creation_descriptor_close_failure_priority(
        self,
    ) -> None:
        actual_open = os.open
        actual_close = os.close
        actual_fstat = os.fstat
        actual_write = os.write
        actual_reject_acl = project_journal._reject_runtime_extended_acl

        for descriptor in ("source", "destination", "directory"):
            for has_active_error in (False, True):
                with self.subTest(
                    descriptor=descriptor,
                    has_active_error=has_active_error,
                ):
                    source = self.make_native_git_copy(
                        f"snapshot-close-{descriptor}-{has_active_error}"
                    )
                    expected_identity = project_journal._git_source_identity(
                        source.stat()
                    )
                    owner = tempfile.TemporaryDirectory(
                        prefix="project-journal-test-snapshot-close-",
                        dir=self.root,
                    )
                    locator = pathlib.Path(owner.name).resolve()
                    snapshot = locator / "git"
                    opened_fds: dict[str, int] = {}
                    close_failed = False
                    interrupted = False
                    close_error = OSError(
                        errno.EIO,
                        f"simulated {descriptor} descriptor close failure",
                    )
                    active_error = (
                        LegacyInterrupt(
                            f"simulated {descriptor} snapshot creation failure"
                        )
                        if has_active_error
                        else None
                    )
                    active_args = active_error.args if active_error is not None else ()

                    def track_open(
                        path: os.PathLike[str] | str,
                        flags: int,
                        mode: int = 0o777,
                        *,
                        dir_fd: int | None = None,
                    ) -> int:
                        fd = actual_open(path, flags, mode, dir_fd=dir_fd)
                        candidate = pathlib.Path(path)
                        if candidate == source:
                            opened_fds["source"] = fd
                        elif candidate == snapshot:
                            opened_fds["destination"] = fd
                        elif candidate == locator:
                            opened_fds["directory"] = fd
                        return fd

                    def close_target_then_fail(fd: int) -> None:
                        nonlocal close_failed
                        actual_close(fd)
                        if not close_failed and opened_fds.get(descriptor) == fd:
                            close_failed = True
                            raise close_error

                    def interrupt_source_fstat(fd: int) -> os.stat_result:
                        nonlocal interrupted
                        if (
                            active_error is not None
                            and descriptor == "source"
                            and not interrupted
                            and opened_fds.get("source") == fd
                        ):
                            interrupted = True
                            raise active_error
                        return actual_fstat(fd)

                    def interrupt_destination_write(
                        fd: int,
                        data: bytes,
                    ) -> int:
                        nonlocal interrupted
                        if (
                            active_error is not None
                            and descriptor == "destination"
                            and not interrupted
                            and opened_fds.get("destination") == fd
                        ):
                            interrupted = True
                            raise active_error
                        return actual_write(fd, data)

                    def interrupt_directory_acl(
                        fd: int,
                        path: pathlib.Path,
                        subject: str,
                    ) -> None:
                        nonlocal interrupted
                        if (
                            active_error is not None
                            and descriptor == "directory"
                            and not interrupted
                            and pathlib.Path(path) == locator
                        ):
                            interrupted = True
                            raise active_error
                        actual_reject_acl(fd, path, subject)

                    try:
                        with contextlib.ExitStack() as stack:
                            stack.enter_context(
                                mock.patch.object(
                                    project_journal.tempfile,
                                    "TemporaryDirectory",
                                    return_value=owner,
                                )
                            )
                            for target, name, side_effect in (
                                (project_journal.os, "open", track_open),
                                (
                                    project_journal.os,
                                    "close",
                                    close_target_then_fail,
                                ),
                                (
                                    project_journal.os,
                                    "fstat",
                                    interrupt_source_fstat,
                                ),
                                (
                                    project_journal.os,
                                    "write",
                                    interrupt_destination_write,
                                ),
                                (
                                    project_journal,
                                    "_reject_runtime_extended_acl",
                                    interrupt_directory_acl,
                                ),
                            ):
                                stack.enter_context(
                                    mock.patch.object(
                                        target,
                                        name,
                                        side_effect=side_effect,
                                    )
                                )
                            try:
                                project_journal._snapshot_git_executable(
                                    source,
                                    expected_source_identity=expected_identity,
                                    deadline=time.monotonic() + 5,
                                )
                            except BaseException as exc:
                                raised_error = exc
                            else:
                                self.fail("expected descriptor close failure")

                        expected_error = active_error or close_error
                        self.assertIs(raised_error, expected_error)
                        self.assertEqual(raised_error.args, expected_error.args)
                        self.assertTrue(close_failed)
                        self.assertEqual(interrupted, has_active_error)
                        notes = "\n".join(getattr(raised_error, "__notes__", ()))
                        self.assertIn(
                            f"{descriptor} descriptor cleanup failed",
                            notes,
                        )
                        self.assertIn(str(close_error), notes)
                        self.assertFalse(locator.exists())
                        traceback_names = self.exception_traceback_names(raised_error)
                        if active_error is not None:
                            self.assertEqual(raised_error.args, active_args)
                            self.assertTrue(
                                any(
                                    name.startswith("interrupt_")
                                    for name in traceback_names
                                )
                            )
                            self.assertNotIn(
                                "_close_git_runtime_snapshot_descriptor_preserving_error",
                                traceback_names,
                            )
                        else:
                            self.assertIn(
                                "close_target_then_fail",
                                traceback_names,
                            )
                    finally:
                        owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX Git runtime contract")
    def test_git_snapshot_creation_cleanup_failure_preserves_original_error(
        self,
    ) -> None:
        fake_git = self.make_native_git_copy("snapshot-creation-git")
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        retained_owner = tempfile.TemporaryDirectory(
            prefix="project-journal-test-snapshot-creation-"
        )
        failing_owner = mock.Mock()
        failing_owner.name = retained_owner.name
        cleanup_error = OSError(
            errno.EIO,
            "simulated snapshot creation cleanup failure",
        )
        failing_owner.cleanup.side_effect = cleanup_error
        original_error = OSError(
            errno.EACCES,
            "original snapshot creation failure",
        )
        original_args = original_error.args

        def interrupt_snapshot_creation(*_args: object, **_kwargs: object) -> None:
            raise original_error

        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal.shutil,
                "which",
                return_value=str(fake_git),
            ):
                with mock.patch.object(
                    project_journal.tempfile,
                    "TemporaryDirectory",
                    return_value=failing_owner,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_reject_runtime_extended_acl",
                        side_effect=interrupt_snapshot_creation,
                    ):
                        try:
                            project_journal._initialize_git_runtime()
                        except OSError as exc:
                            raised_error = exc
                        else:
                            self.fail("expected original snapshot creation failure")

            self.assertIs(raised_error, original_error)
            self.assertIs(type(raised_error), type(original_error))
            self.assertEqual(raised_error.args, original_args)
            notes = "\n".join(getattr(raised_error, "__notes__", ()))
            self.assertIn(
                "Git runtime snapshot cleanup-incomplete after snapshot creation",
                notes,
            )
            self.assertIn(str(retained_owner.name), notes)
            self.assertIn(str(cleanup_error), notes)
            traceback_names = self.exception_traceback_names(raised_error)
            self.assertIn("interrupt_snapshot_creation", traceback_names)
            self.assertNotIn("_cleanup_git_snapshot_owner", traceback_names)
            failing_owner.cleanup.assert_called_once_with()
            self.assertTrue(pathlib.Path(retained_owner.name).exists())
            self.assertIsNone(project_journal._GIT_RUNTIME)
            self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
        finally:
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error
            retained_owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX Git runtime contract")
    def test_git_version_cleanup_failure_preserves_original_error(self) -> None:
        fake_git = self.make_native_git_copy("version-cleanup-git")
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        retained_owner = tempfile.TemporaryDirectory(
            prefix="project-journal-test-version-cleanup-"
        )
        failing_owner = mock.Mock()
        failing_owner.name = retained_owner.name
        cleanup_error = OSError(
            errno.EIO,
            "simulated version snapshot cleanup failure",
        )
        failing_owner.cleanup.side_effect = cleanup_error
        original_error = LegacyUnsupportedPlatform(
            "original version probe failure",
        )
        original_args = original_error.args

        def interrupt_version_probe(
            *_args: object,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            raise original_error

        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal.shutil,
                "which",
                return_value=str(fake_git),
            ):
                with mock.patch.object(
                    project_journal.tempfile,
                    "TemporaryDirectory",
                    return_value=failing_owner,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_capture_bounded_process",
                        side_effect=interrupt_version_probe,
                    ):
                        try:
                            project_journal._initialize_git_runtime()
                        except LegacyUnsupportedPlatform as exc:
                            raised_error = exc
                        else:
                            self.fail("expected original version probe failure")

            self.assertIs(raised_error, original_error)
            self.assertIs(type(raised_error), LegacyUnsupportedPlatform)
            self.assertEqual(
                raised_error.code,
                project_journal.UnsupportedPlatform.code,
            )
            self.assertEqual(raised_error.args, original_args)
            notes = "\n".join(getattr(raised_error, "__notes__", ()))
            self.assertIn(
                "Git runtime snapshot cleanup-incomplete after the bounded "
                "Git version gate",
                notes,
            )
            self.assertIn(str(retained_owner.name), notes)
            self.assertIn(str(cleanup_error), notes)
            traceback_names = self.exception_traceback_names(raised_error)
            self.assertIn("interrupt_version_probe", traceback_names)
            self.assertNotIn("_cleanup_git_snapshot_owner", traceback_names)
            failing_owner.cleanup.assert_called_once_with()
            self.assertTrue(pathlib.Path(retained_owner.name).exists())
            self.assertIsNone(project_journal._GIT_RUNTIME)
            self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
        finally:
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error
            retained_owner.cleanup()

    @unittest.skipUnless(os.name == "posix", "POSIX Git runtime contract")
    def test_git_final_validation_cleanup_failure_preserves_original_error(
        self,
    ) -> None:
        fake_git = self.make_native_git_copy("final-validation-cleanup-git")
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        retained_owner = tempfile.TemporaryDirectory(
            prefix="project-journal-test-final-validation-cleanup-"
        )
        failing_owner = mock.Mock()
        failing_owner.name = retained_owner.name
        cleanup_error = OSError(
            errno.EIO,
            "simulated final validation cleanup failure",
        )
        failing_owner.cleanup.side_effect = cleanup_error
        original_error = LegacyInterrupt(
            "original final snapshot validation interruption",
        )
        original_args = original_error.args
        version_result = subprocess.CompletedProcess(
            [str(fake_git), "--version"],
            0,
            b"git version 2.45.1\n",
            b"",
        )

        def interrupt_final_validation(
            *_args: object,
            **_kwargs: object,
        ) -> None:
            raise original_error

        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal.shutil,
                "which",
                return_value=str(fake_git),
            ):
                with mock.patch.object(
                    project_journal.tempfile,
                    "TemporaryDirectory",
                    return_value=failing_owner,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_capture_bounded_process",
                        return_value=version_result,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_verify_git_runtime_snapshot",
                            side_effect=interrupt_final_validation,
                        ):
                            try:
                                project_journal._initialize_git_runtime()
                            except LegacyInterrupt as exc:
                                raised_error = exc
                            else:
                                self.fail(
                                    "expected original final validation interruption"
                                )

            self.assertIs(raised_error, original_error)
            self.assertIs(type(raised_error), LegacyInterrupt)
            self.assertEqual(raised_error.args, original_args)
            notes = "\n".join(getattr(raised_error, "__notes__", ()))
            self.assertIn(
                "Git runtime snapshot cleanup-incomplete after final "
                "Git snapshot validation",
                notes,
            )
            self.assertIn(str(retained_owner.name), notes)
            self.assertIn(str(cleanup_error), notes)
            traceback_names = self.exception_traceback_names(raised_error)
            self.assertIn("interrupt_final_validation", traceback_names)
            self.assertNotIn("_cleanup_git_snapshot_owner", traceback_names)
            failing_owner.cleanup.assert_called_once_with()
            self.assertTrue(pathlib.Path(retained_owner.name).exists())
            self.assertIsNone(project_journal._GIT_RUNTIME)
            self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
        finally:
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error
            retained_owner.cleanup()

    def test_git_snapshot_cleanup_without_primary_reports_cleanup_failure(
        self,
    ) -> None:
        retained_owner = tempfile.TemporaryDirectory(
            prefix="project-journal-test-standalone-cleanup-"
        )
        failing_owner = mock.Mock()
        failing_owner.name = retained_owner.name
        cleanup_error = OSError(
            errno.EACCES,
            "simulated standalone snapshot cleanup failure",
        )
        failing_owner.cleanup.side_effect = cleanup_error
        try:
            with self.assertRaises(
                project_journal.UnsupportedGitVersion,
            ) as raised:
                project_journal._cleanup_git_snapshot_owner(
                    failing_owner,
                    "standalone rejection",
                    None,
                )

            self.assertIs(raised.exception.__cause__, cleanup_error)
            self.assertIn(
                "cleanup-incomplete after standalone rejection", str(raised.exception)
            )
            self.assertIn(str(retained_owner.name), str(raised.exception))
            failing_owner.cleanup.assert_called_once_with()

            cleanup_interrupt = LegacyInterrupt(
                "simulated standalone snapshot cleanup interruption",
            )
            failing_owner.cleanup.side_effect = cleanup_interrupt
            try:
                project_journal._cleanup_git_snapshot_owner(
                    failing_owner,
                    "standalone interruption",
                    None,
                )
            except LegacyInterrupt as exc:
                raised_interrupt = exc
            else:
                self.fail("expected standalone snapshot cleanup interruption")

            self.assertIs(raised_interrupt, cleanup_interrupt)
            interrupt_notes = "\n".join(getattr(raised_interrupt, "__notes__", ()))
            self.assertIn(
                "cleanup-incomplete after standalone interruption",
                interrupt_notes,
            )
            self.assertIn(str(retained_owner.name), interrupt_notes)
            self.assertEqual(failing_owner.cleanup.call_count, 2)
        finally:
            retained_owner.cleanup()

    def test_git_launch_is_cleaned_when_selector_creation_is_interrupted(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-selector-interrupt")
        actual_mkdtemp = tempfile.mkdtemp
        launch_directories: list[pathlib.Path] = []

        def capture_launch_directory(*args: object, **kwargs: object) -> str:
            directory = actual_mkdtemp(*args, **kwargs)
            if pathlib.Path(directory).name.startswith("project-journal-git-launch-"):
                launch_directories.append(pathlib.Path(directory))
            return directory

        try:
            with mock.patch.object(
                project_journal.tempfile,
                "mkdtemp",
                side_effect=capture_launch_directory,
            ):
                with mock.patch.object(
                    project_journal.selectors,
                    "DefaultSelector",
                    side_effect=KeyboardInterrupt(
                        "injected selector creation interruption"
                    ),
                ):
                    with self.assertRaises(KeyboardInterrupt):
                        project_journal._capture_bounded_process(
                            [str(runtime.executable), "probe"],
                            env={"PATH": os.environ.get("PATH", "")},
                            verified_runtime=runtime,
                            timeout_seconds=2,
                            stdout_limit=1024,
                            stderr_limit=1024,
                            stdout_overflow_error="stdout overflow",
                            stderr_overflow_error="stderr overflow",
                            timeout_error="launch timed out",
                            operation="selector-interrupted Git launch",
                        )

            self.assertEqual(len(launch_directories), 1)
            self.assertFalse(launch_directories[0].exists())
        finally:
            for directory in launch_directories:
                if directory.exists():
                    os.chmod(directory, 0o700)
                    shutil.rmtree(directory)
            runtime.snapshot_owner.cleanup()

    def test_git_launch_is_retained_when_process_start_is_interrupted(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-popen-interrupt")
        observed_launch: pathlib.Path | None = None
        interruption = LegacyInterrupt("injected process-start interruption")
        original_args = interruption.args

        def interrupt_process_start(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            nonlocal observed_launch
            observed_launch = pathlib.Path(str(kwargs["executable"]))
            raise interruption

        try:
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=interrupt_process_start,
            ):
                with self.assertRaises(LegacyInterrupt) as raised:
                    project_journal._capture_bounded_process(
                        [str(runtime.executable), "probe"],
                        env={"PATH": os.environ.get("PATH", "")},
                        verified_runtime=runtime,
                        timeout_seconds=2,
                        stdout_limit=1024,
                        stderr_limit=1024,
                        stdout_overflow_error="stdout overflow",
                        stderr_overflow_error="stderr overflow",
                        timeout_error="launch timed out",
                        operation="process-start-interrupted Git launch",
                    )

            self.assertIs(raised.exception, interruption)
            self.assertEqual(raised.exception.args, original_args)
            self.assertIsNotNone(observed_launch)
            assert observed_launch is not None
            self.assertTrue(observed_launch.exists())
            detail = "\n".join(getattr(raised.exception, "__notes__", ()))
            self.assertIn("lifecycle state is unverified", detail)
            self.assertIn(str(observed_launch.parent), detail)
        finally:
            if observed_launch is not None and observed_launch.parent.exists():
                os.chmod(observed_launch.parent, 0o700)
                shutil.rmtree(observed_launch.parent)
            runtime.snapshot_owner.cleanup()

    def test_git_launch_is_cleaned_when_ownership_setup_is_interrupted(
        self,
    ) -> None:
        runtime = self.make_fake_git_runtime("launch-ownership-handoff")
        actual_mkdtemp = tempfile.mkdtemp
        launch_directories: list[pathlib.Path] = []

        def capture_launch_directory(*args: object, **kwargs: object) -> str:
            directory = actual_mkdtemp(*args, **kwargs)
            if pathlib.Path(directory).name.startswith("project-journal-git-launch-"):
                launch_directories.append(pathlib.Path(directory))
            return directory

        try:
            with mock.patch.object(
                project_journal.tempfile,
                "mkdtemp",
                side_effect=capture_launch_directory,
            ):
                with mock.patch.object(
                    project_journal._ProcessOwnership,
                    "for_process",
                    side_effect=KeyboardInterrupt(
                        "injected pre-Popen ownership interruption"
                    ),
                ):
                    with mock.patch.object(
                        project_journal.subprocess,
                        "Popen",
                    ) as popen:
                        with self.assertRaises(KeyboardInterrupt):
                            project_journal._capture_bounded_process(
                                [str(runtime.executable), "probe"],
                                env={"PATH": os.environ.get("PATH", "")},
                                verified_runtime=runtime,
                                timeout_seconds=2,
                                stdout_limit=1024,
                                stderr_limit=1024,
                                stdout_overflow_error="stdout overflow",
                                stderr_overflow_error="stderr overflow",
                                timeout_error="launch timed out",
                                operation="ownership-setup Git launch",
                            )

            popen.assert_not_called()
            self.assertEqual(len(launch_directories), 1)
            self.assertFalse(launch_directories[0].exists())
        finally:
            for directory in launch_directories:
                if directory.exists():
                    os.chmod(directory, 0o700)
                    shutil.rmtree(directory)
            runtime.snapshot_owner.cleanup()

    def test_git_version_gate_executes_bound_copy_during_snapshot_replacement(
        self,
    ) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        self.assertIsNotNone(old_runtime)
        assert old_runtime is not None
        fake_git = self.root / "version-gate-git"
        shutil.copyfile(old_runtime.source_executable, fake_git)
        fake_git.chmod(0o755)
        attacker = self.root / "version-gate-attacker"
        attacker.write_text(
            "#!/bin/sh\nprintf 'git version 1.0.0\\n'\n",
            encoding="utf-8",
        )
        attacker.chmod(0o755)
        actual_popen = subprocess.Popen
        actual_snapshot = project_journal._snapshot_git_executable
        observed_snapshot: pathlib.Path | None = None
        replaced = False

        def capture_snapshot(
            *args: object,
            **kwargs: object,
        ) -> tuple[object, ...]:
            nonlocal observed_snapshot
            snapshot_result = actual_snapshot(*args, **kwargs)
            observed_snapshot = snapshot_result[0]
            return snapshot_result

        def replace_snapshot_during_version_gate(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            nonlocal replaced
            self.assertEqual(pathlib.Path(argv[0]), fake_git.resolve())
            self.assertIsNotNone(observed_snapshot)
            assert observed_snapshot is not None
            snapshot = observed_snapshot
            launch = pathlib.Path(str(kwargs["executable"]))
            backup = snapshot.with_name("verified-snapshot-backup")
            self.assertNotEqual(launch, snapshot)
            os.replace(snapshot, backup)
            os.replace(attacker, snapshot)
            replaced = True
            try:
                return actual_popen(argv, *args, **kwargs)
            finally:
                snapshot.unlink()
                os.replace(backup, snapshot)

        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal.shutil,
                "which",
                return_value=str(fake_git),
            ):
                with mock.patch.object(
                    project_journal,
                    "_snapshot_git_executable",
                    side_effect=capture_snapshot,
                ):
                    with mock.patch.object(
                        project_journal.subprocess,
                        "Popen",
                        side_effect=replace_snapshot_during_version_gate,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_verify_git_runtime_snapshot",
                            wraps=project_journal._verify_git_runtime_snapshot,
                        ) as verify:
                            project_journal._initialize_git_runtime()

            self.assertTrue(replaced)
            self.assertIsNotNone(verify.call_args.kwargs["deadline"])
            self.assertEqual(
                verify.call_args.kwargs["deadline_error"],
                "selected Git executable snapshot exceeded its shared deadline",
            )
            self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
            runtime = project_journal._GIT_RUNTIME
            self.assertIsNotNone(runtime)
            assert runtime is not None
            self.assertEqual(runtime.version, old_runtime.version)
        finally:
            runtime = project_journal._GIT_RUNTIME
            if runtime is not None and runtime is not old_runtime:
                runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

    def test_git_runtime_snapshot_rejects_fifo_replacement_without_blocking(
        self,
    ) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        fake_git = self.root / "git-fifo-race"
        fake_git.write_text(
            "#!/bin/sh\nprintf 'git version 2.45.1\\n'\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        resolved_fake_git = fake_git.resolve()
        fifo = self.root / "git-fifo-replacement"
        os.mkfifo(fifo)
        actual_open = os.open
        replaced = False

        def replace_with_fifo(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal replaced
            if pathlib.Path(path) == resolved_fake_git and not replaced:
                replaced = True
                self.assertTrue(flags & project_journal.os.O_NONBLOCK)
                self.assertTrue(flags & project_journal.os.O_NOFOLLOW)
                os.replace(fifo, fake_git)
            return actual_open(path, flags, mode, dir_fd=dir_fd)

        try:
            project_journal._GIT_RUNTIME = None
            project_journal._GIT_RUNTIME_ERROR = None
            started = time.monotonic()
            with mock.patch.object(
                project_journal.shutil,
                "which",
                return_value=str(fake_git),
            ):
                with mock.patch.object(
                    project_journal.os,
                    "open",
                    side_effect=replace_with_fifo,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_capture_bounded_process",
                    ) as capture:
                        project_journal._initialize_git_runtime()

            self.assertLess(time.monotonic() - started, 1.0)
            self.assertTrue(replaced)
            self.assertIsNone(project_journal._GIT_RUNTIME)
            self.assertIn(
                "not a regular file",
                str(project_journal._GIT_RUNTIME_ERROR),
            )
            capture.assert_not_called()
        finally:
            runtime = project_journal._GIT_RUNTIME
            if runtime is not None and runtime is not old_runtime:
                runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

    def test_git_runtime_snapshot_requires_secure_open_primitives(self) -> None:
        source = self.root / "git-primitives"
        source.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        source.chmod(0o755)
        expected = project_journal._git_source_identity(source.stat())

        for primitive in ("O_NOFOLLOW", "O_NONBLOCK"):
            with self.subTest(primitive=primitive):
                with mock.patch.object(project_journal.os, primitive, 0):
                    with self.assertRaisesRegex(
                        project_journal.UnsupportedPlatform,
                        primitive,
                    ):
                        project_journal._snapshot_git_executable(
                            source,
                            expected_source_identity=expected,
                            deadline=time.monotonic() + 5,
                        )

    def test_git_runtime_snapshot_enforces_size_and_deadline_bounds(self) -> None:
        source = self.root / "git-bounds"
        source.write_bytes(b"0123456789")
        source.chmod(0o755)
        expected = project_journal._git_source_identity(source.stat())

        with mock.patch.object(project_journal, "MAX_GIT_EXECUTABLE_BYTES", 5):
            with self.assertRaisesRegex(OSError, "exceeds 5 bytes"):
                project_journal._snapshot_git_executable(
                    source,
                    expected_source_identity=expected,
                    deadline=time.monotonic() + 5,
                )

        actual_read = os.read
        grew = False

        def grow_after_first_read(fd: int, size: int) -> bytes:
            nonlocal grew
            chunk = actual_read(fd, size)
            if chunk and not grew:
                grew = True
                with source.open("ab") as handle:
                    handle.write(b"x")
            return chunk

        with mock.patch.object(project_journal, "MAX_GIT_EXECUTABLE_BYTES", 10):
            with mock.patch.object(
                project_journal.os,
                "read",
                side_effect=grow_after_first_read,
            ):
                with self.assertRaisesRegex(OSError, "exceeds 10 bytes"):
                    project_journal._snapshot_git_executable(
                        source,
                        expected_source_identity=expected,
                        deadline=time.monotonic() + 5,
                    )
        self.assertTrue(grew)

        source.write_bytes(b"0123456789")
        source.chmod(0o755)
        expected = project_journal._git_source_identity(source.stat())
        clock = {"now": 100.0}
        advanced = False

        def advance_after_first_read(fd: int, size: int) -> bytes:
            nonlocal advanced
            chunk = actual_read(fd, size)
            if chunk and not advanced:
                advanced = True
                clock["now"] = 102.0
            return chunk

        with mock.patch.object(
            project_journal.time,
            "monotonic",
            side_effect=lambda: clock["now"],
        ):
            with mock.patch.object(
                project_journal.os,
                "read",
                side_effect=advance_after_first_read,
            ):
                with self.assertRaisesRegex(
                    project_journal.UserError,
                    "exceeded its shared deadline",
                ):
                    project_journal._snapshot_git_executable(
                        source,
                        expected_source_identity=expected,
                        deadline=101.0,
                    )
        self.assertTrue(advanced)

        with self.assertRaisesRegex(
            project_journal.UserError,
            "exceeded its shared deadline",
        ):
            project_journal._snapshot_git_executable(
                source,
                expected_source_identity=expected,
                deadline=time.monotonic() - 1,
            )

    def test_script_git_fails_closed_and_discovery_reports_inconclusive(
        self,
    ) -> None:
        repo = self.init_repo()
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-old-git.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )

        shim_dir = self.root / "old-git-bin"
        shim_dir.mkdir()
        shim_log = self.root / "old-git.log"
        shim = shim_dir / "git"
        shim.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                printf '%s|lazy=%s\\n' "$*" "${{GIT_NO_LAZY_FETCH:-}}" >> {shlex.quote(str(shim_log))}
                if [ "$1" = "--version" ]; then
                  if [ "${{GIT_NO_LAZY_FETCH:-}}" != "1" ]; then
                    exit 91
                  fi
                  printf 'git version 2.44.9\\n'
                  exit 0
                fi
                exit 92
                """
            ),
            encoding="utf-8",
        )
        shim.chmod(0o755)

        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
            env={
                "PATH": str(shim_dir),
            },
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["repo"])
        self.assertEqual(rows[0]["candidate_cwd"], str(repo))
        self.assertEqual(rows[0]["discovery_status"], "inconclusive")
        self.assertEqual(rows[0]["adoption_status"], "inconclusive")
        self.assertEqual(
            rows[0]["adoption_error"]["code"],
            "repository_resolution_failed",
        )
        self.assertIn(
            "selected Git executable bytes identify a script wrapper",
            rows[0]["adoption_error"]["message"],
        )
        resolution_error = rows[0]["discovery_error"]["repo_resolution"]
        self.assertEqual(
            resolution_error["code"],
            "repository_resolution_failed",
        )
        self.assertEqual(
            resolution_error["resolution_reason"],
            "git_marker_present",
        )
        self.assertEqual(resolution_error["marker_kind"], "directory")
        self.assertNotIn("marker_path", resolution_error)
        self.assertEqual(
            pathlib.Path(resolution_error["marker_path_hint"]),
            repo / ".git",
        )
        self.assertEqual(
            resolution_error["marker_path_status"],
            "path_unverified",
        )
        self.assertIsNone(rows[0]["index_ignored"])
        self.assertFalse(shim_log.exists())

    def test_git_version_probe_timeout_retries_and_then_succeeds(self) -> None:
        attempts = 0

        def timeout_then_succeed(
            *_args: object,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise project_journal.UserError(
                    "simulated transient Git version timeout"
                )
            return success

        with self.isolated_git_runtime_initialization(
            "version-timeout-retry-git"
        ) as fake_git:
            success = subprocess.CompletedProcess(
                [str(fake_git), "--version"],
                0,
                b"git version 2.45.1\n",
                b"",
            )
            with mock.patch.object(
                project_journal,
                "_capture_bounded_process",
                side_effect=timeout_then_succeed,
            ) as capture:
                with self.assertRaises(project_journal.GitVersionProbeError) as raised:
                    project_journal._initialize_git_runtime()

                self.assertIn("inconclusive", str(raised.exception))
                self.assertIsNone(project_journal._GIT_RUNTIME)
                self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
                project_journal._initialize_git_runtime()

            self.assertEqual(attempts, 2)
            self.assertEqual(capture.call_count, 2)
            for call in capture.call_args_list:
                self.assertEqual(
                    call.kwargs["timeout_seconds"],
                    project_journal.GIT_EXECUTABLE_SNAPSHOT_TIMEOUT_SECONDS,
                )
                self.assertIsNotNone(call.kwargs["deadline"])
                self.assertIn("shared deadline", call.kwargs["timeout_error"])
            self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
            self.assertIsNotNone(project_journal._GIT_RUNTIME)

    def test_git_version_probe_repeated_timeouts_are_not_cached(self) -> None:
        attempts = 0

        def always_timeout(
            *_args: object,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[bytes]:
            nonlocal attempts
            attempts += 1
            raise project_journal.UserError(
                f"simulated transient Git version timeout {attempts}"
            )

        with self.isolated_git_runtime_initialization("version-repeated-timeout-git"):
            with mock.patch.object(
                project_journal,
                "_capture_bounded_process",
                side_effect=always_timeout,
            ) as capture:
                for expected_attempt in (1, 2):
                    with self.assertRaises(
                        project_journal.GitVersionProbeError
                    ) as raised:
                        project_journal._initialize_git_runtime()
                    self.assertIn(
                        f"timeout {expected_attempt}",
                        str(raised.exception),
                    )
                    self.assertIsNone(project_journal._GIT_RUNTIME)
                    self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)

            self.assertEqual(attempts, 2)
            self.assertEqual(capture.call_count, 2)

    def test_git_version_probe_malformed_is_transient_but_old_is_cached(
        self,
    ) -> None:
        with self.isolated_git_runtime_initialization(
            "version-classification-git"
        ) as fake_git:
            malformed = subprocess.CompletedProcess(
                [str(fake_git), "--version"],
                0,
                b"not a Git version\n",
                b"",
            )
            outdated = subprocess.CompletedProcess(
                [str(fake_git), "--version"],
                0,
                b"git version 2.44.9\n",
                b"",
            )
            with mock.patch.object(
                project_journal,
                "_capture_bounded_process",
                side_effect=(malformed, outdated),
            ) as capture:
                with self.assertRaises(project_journal.GitVersionProbeError) as raised:
                    project_journal._initialize_git_runtime()
                self.assertIn(
                    "unsupported response",
                    str(raised.exception),
                )
                self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)

                project_journal._initialize_git_runtime()
                self.assertIsInstance(
                    project_journal._GIT_RUNTIME_ERROR,
                    project_journal.UnsupportedGitVersion,
                )
                self.assertIn(
                    "Git >= 2.45 is required",
                    str(project_journal._GIT_RUNTIME_ERROR),
                )

                project_journal._initialize_git_runtime()

            self.assertEqual(capture.call_count, 2)
            self.assertIsNone(project_journal._GIT_RUNTIME)

    def test_git_version_probe_nonzero_is_transient_and_not_cached(self) -> None:
        with self.isolated_git_runtime_initialization(
            "version-nonzero-git"
        ) as fake_git:
            failure = subprocess.CompletedProcess(
                [str(fake_git), "--version"],
                71,
                b"",
                b"temporary loader failure\n",
            )
            with mock.patch.object(
                project_journal,
                "_capture_bounded_process",
                return_value=failure,
            ) as capture:
                with self.assertRaises(project_journal.GitVersionProbeError) as raised:
                    project_journal._initialize_git_runtime()

            self.assertIn("temporary loader failure", str(raised.exception))
            self.assertIsNone(project_journal._GIT_RUNTIME)
            self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
            capture.assert_called_once()

    def test_git_version_probe_obeys_exhausted_shared_deadline(self) -> None:
        with self.isolated_git_runtime_initialization(
            "version-shared-deadline-git"
        ) as fake_git:
            prepared_snapshot = project_journal._snapshot_git_executable(
                fake_git,
                expected_source_identity=project_journal._git_source_identity(
                    fake_git.stat()
                ),
                deadline=time.monotonic() + 5,
            )
            snapshot_owner = prepared_snapshot[-1]
            snapshot_locator = pathlib.Path(snapshot_owner.name)
            clock = {"now": 100.0}

            def exhaust_after_snapshot(
                *_args: object,
                deadline: float,
                **_kwargs: object,
            ) -> tuple[object, ...]:
                self.assertEqual(deadline, 105.0)
                clock["now"] = deadline
                return prepared_snapshot

            try:
                with mock.patch.object(
                    project_journal,
                    "_snapshot_git_executable",
                    side_effect=exhaust_after_snapshot,
                ):
                    with mock.patch.object(
                        project_journal.time,
                        "monotonic",
                        side_effect=lambda: clock["now"],
                    ):
                        with mock.patch.object(
                            project_journal.subprocess,
                            "Popen",
                        ) as popen:
                            with self.assertRaises(
                                project_journal.GitVersionProbeError
                            ) as raised:
                                project_journal._initialize_git_runtime()

                self.assertIn("shared deadline", str(raised.exception))
                self.assertIsNone(project_journal._GIT_RUNTIME)
                self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
                self.assertFalse(snapshot_locator.exists())
                popen.assert_not_called()
            finally:
                snapshot_owner.cleanup()

    def test_native_old_git_version_fails_closed_before_repository_commands(
        self,
    ) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        self.assertIsNotNone(old_runtime)
        assert old_runtime is not None
        fake_git = self.root / "native-old-git"
        shutil.copyfile(old_runtime.source_executable, fake_git)
        fake_git.chmod(0o755)
        old_version = subprocess.CompletedProcess(
            [str(fake_git), "--version"],
            0,
            b"git version 2.44.9\n",
            b"",
        )

        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal.shutil,
                "which",
                return_value=str(fake_git),
            ):
                with mock.patch.object(
                    project_journal,
                    "_capture_bounded_process",
                    return_value=old_version,
                ) as capture:
                    project_journal._initialize_git_runtime()

            self.assertIsNone(project_journal._GIT_RUNTIME)
            self.assertIsInstance(
                project_journal._GIT_RUNTIME_ERROR,
                project_journal.UnsupportedGitVersion,
            )
            self.assertIn(
                "Git >= 2.45 is required",
                str(project_journal._GIT_RUNTIME_ERROR),
            )
            capture.assert_called_once()
            self.assertEqual(capture.call_args.kwargs["env"]["GIT_NO_LAZY_FETCH"], "1")
        finally:
            runtime = project_journal._GIT_RUNTIME
            if runtime is not None and runtime is not old_runtime:
                runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

    def test_adoption_status_ignores_poisoned_git_repo_and_object_env(
        self,
    ) -> None:
        requested = self.init_repo("requested")
        attacker = self.init_repo("attacker")
        attacker_journal = self.write_journal(
            attacker,
            "docs/project_journal/2026/05/2026-05-05-attacker-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Attacker Journal",
            status="active",
            updated="2026-05-05",
        )
        add = run_git(
            attacker,
            "add",
            "--",
            str(attacker_journal.relative_to(attacker)),
        )
        self.assertEqual(add.returncode, 0, add.stderr)

        poison_config = self.root / "poison-gitconfig"
        poison_config.write_text(
            textwrap.dedent(
                f"""\
                [core]
                    worktree = {attacker}
                [remote "origin"]
                    promisor = true
                """
            ),
            encoding="utf-8",
        )
        poison = {
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(attacker / ".git/objects"),
            "GIT_COMMON_DIR": str(attacker / ".git"),
            "GIT_CONFIG": str(poison_config),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_GLOBAL": str(poison_config),
            "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_NOSYSTEM": "0",
            "GIT_CONFIG_SYSTEM": str(poison_config),
            "GIT_CONFIG_VALUE_0": str(attacker),
            "GIT_DIR": str(attacker / ".git"),
            "GIT_INDEX_FILE": str(attacker / ".git/index"),
            "GIT_NO_LAZY_FETCH": "0",
            "GIT_OBJECT_DIRECTORY": str(attacker / ".git/objects"),
            "GIT_WORK_TREE": str(attacker),
        }

        result = self.run_cli(
            "adoption-status",
            "--repo",
            str(requested),
            env=poison,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        status = json.loads(result.stdout)
        self.assertEqual(pathlib.Path(status["repo"]), requested.resolve())
        self.assertFalse(status["tracked_journal_adopted"])
        self.assertEqual(status["valid_tracked_journal_count"], 0)

    def test_missing_index_blob_does_not_lazy_fetch_under_poisoned_env(
        self,
    ) -> None:
        repo = self.init_repo()
        remote = self.root / "remote.git"
        remote.mkdir()
        init_remote = run_git(remote, "init", "--bare")
        self.assertEqual(init_remote.returncode, 0, init_remote.stderr)

        marker = self.root / "upload-pack-ran"
        upload_pack = self.root / "upload-pack"
        upload_pack.write_text(
            f"#!/bin/sh\nprintf invoked > {marker}\nexit 1\n",
            encoding="utf-8",
        )
        upload_pack.chmod(0o755)
        for key, value in (
            ("core.repositoryFormatVersion", "1"),
            ("extensions.partialClone", "origin"),
            ("remote.origin.promisor", "true"),
            ("remote.origin.partialCloneFilter", "blob:none"),
            ("remote.origin.url", str(remote)),
            ("remote.origin.uploadpack", str(upload_pack)),
        ):
            configured = run_git(repo, "config", key, value)
            self.assertEqual(configured.returncode, 0, configured.stderr)

        missing_oid = "a" * 40
        rel_path = "docs/project_journal/2026/05/2026-05-05-missing-object-a1b2c3.md"
        staged = run_git(
            repo,
            "update-index",
            "--add",
            "--info-only",
            "--cacheinfo",
            f"100644,{missing_oid},{rel_path}",
        )
        self.assertEqual(staged.returncode, 0, staged.stderr)

        result = self.run_cli(
            "adoption-status",
            "--repo",
            str(repo),
            env={"GIT_NO_LAZY_FETCH": "0"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("index blob is missing", result.stderr)
        self.assertFalse(marker.exists())

    def test_adoption_status_rejects_invalid_tracked_journal_entry(self) -> None:
        repo = self.init_repo()
        journal = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-invalid-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Invalid Work",
            status="paused",
            updated="2026-05-05",
        )
        add = run_git(repo, "add", "--", str(journal.relative_to(repo)))
        self.assertEqual(add.returncode, 0, add.stderr)

        status = self.adoption_status(repo)

        self.assertFalse(status["tracked_journal_adopted"])
        self.assertEqual(status["tracked_non_generated_journal_count"], 1)
        self.assertEqual(status["valid_tracked_journal_count"], 0)

    def test_adoption_status_invalidates_every_entry_in_duplicate_id_group(
        self,
    ) -> None:
        repo = self.init_repo()
        first = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-first-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="First Duplicate",
            status="active",
            updated="2026-05-05",
        )
        second = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-06-second-d4e5f6.md",
            entry_id="20260505-a1b2c3",
            title="Second Duplicate",
            status="active",
            updated="2026-05-06",
        )
        add = run_git(
            repo,
            "add",
            "--",
            str(first.relative_to(repo)),
            str(second.relative_to(repo)),
        )
        self.assertEqual(add.returncode, 0, add.stderr)

        status = self.adoption_status(repo)
        _entries, issues, _count = project_journal._load_entries_from_index(repo)

        self.assertFalse(status["tracked_journal_adopted"])
        self.assertEqual(status["tracked_non_generated_journal_count"], 2)
        self.assertEqual(status["valid_tracked_journal_count"], 0)
        duplicate_issues = [issue for issue in issues if "duplicate id" in issue]
        self.assertEqual(len(duplicate_issues), 2)
        self.assertTrue(any(first.name in issue for issue in duplicate_issues))
        self.assertTrue(any(second.name in issue for issue in duplicate_issues))

    def test_adoption_status_uses_staged_invalid_blob_not_valid_worktree(
        self,
    ) -> None:
        repo = self.init_repo()
        journal = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-staged-invalid-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Staged Invalid",
            status="paused",
            updated="2026-05-05",
        )
        add = run_git(repo, "add", "--", str(journal.relative_to(repo)))
        self.assertEqual(add.returncode, 0, add.stderr)
        journal.write_text(
            journal.read_text(encoding="utf-8").replace(
                "status: paused",
                "status: active",
            ),
            encoding="utf-8",
        )

        status = self.adoption_status(repo)

        self.assertFalse(status["tracked_journal_adopted"])
        self.assertEqual(status["tracked_non_generated_journal_count"], 1)
        self.assertEqual(status["valid_tracked_journal_count"], 0)

    def test_adoption_status_uses_staged_valid_blob_not_invalid_worktree(
        self,
    ) -> None:
        repo = self.init_repo()
        journal = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-staged-valid-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Staged Valid",
            status="active",
            updated="2026-05-05",
        )
        add = run_git(repo, "add", "--", str(journal.relative_to(repo)))
        self.assertEqual(add.returncode, 0, add.stderr)
        journal.write_text(
            journal.read_text(encoding="utf-8").replace(
                "status: active",
                "status: paused",
            ),
            encoding="utf-8",
        )

        status = self.adoption_status(repo)

        self.assertTrue(status["tracked_journal_adopted"])
        self.assertEqual(status["tracked_non_generated_journal_count"], 1)
        self.assertEqual(status["valid_tracked_journal_count"], 1)

    def test_adoption_status_rejects_index_symlink_replaced_by_regular_file(
        self,
    ) -> None:
        repo = self.init_repo()
        source = self.write_journal(
            repo,
            "valid-source.md",
            entry_id="20260505-a1b2c3",
            title="Symlink Source",
            status="active",
            updated="2026-05-05",
        )
        journal = repo / "docs/project_journal/2026/05/2026-05-05-symlink-a1b2c3.md"
        journal.parent.mkdir(parents=True)
        journal.symlink_to(source)
        add = run_git(repo, "add", "--", str(journal.relative_to(repo)))
        self.assertEqual(add.returncode, 0, add.stderr)
        journal.unlink()
        journal.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

        status = self.adoption_status(repo)

        self.assertFalse(status["tracked_journal_adopted"])
        self.assertEqual(status["tracked_non_generated_journal_count"], 0)
        self.assertEqual(status["valid_tracked_journal_count"], 0)

    def test_adoption_status_rejects_conflicted_index_entry(self) -> None:
        repo = self.init_repo()
        journal = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-conflict-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Conflict",
            status="active",
            updated="2026-05-05",
        )
        blob = run_git(repo, "hash-object", "-w", str(journal))
        self.assertEqual(blob.returncode, 0, blob.stderr)
        rel_path = journal.relative_to(repo).as_posix()
        index_info = "".join(
            f"100644 {blob.stdout.strip()} {stage}\t{rel_path}\n" for stage in (1, 2, 3)
        )
        update = subprocess.run(
            ["git", "-C", str(repo), "update-index", "--index-info"],
            check=False,
            text=True,
            input=index_info,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(update.returncode, 0, update.stderr)

        status = self.adoption_status(repo)

        self.assertFalse(status["tracked_journal_adopted"])
        self.assertEqual(status["tracked_non_generated_journal_count"], 0)
        self.assertEqual(status["valid_tracked_journal_count"], 0)

    def test_adoption_status_checks_generated_marker_in_index_blob(self) -> None:
        repo = self.init_repo()
        generated = self.run_cli(
            "generate",
            "--repo",
            str(repo),
            "--output",
            "docs/project_journal/custom.md",
        )
        self.assertEqual(generated.returncode, 0, generated.stderr)
        custom_index = repo / "docs/project_journal/custom.md"
        add = run_git(repo, "add", "--", str(custom_index.relative_to(repo)))
        self.assertEqual(add.returncode, 0, add.stderr)
        valid_source = self.write_journal(
            repo,
            "valid-source.md",
            entry_id="20260505-a1b2c3",
            title="Valid Worktree Replacement",
            status="active",
            updated="2026-05-05",
        )
        custom_index.write_text(
            valid_source.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        status = self.adoption_status(repo)

        self.assertFalse(status["tracked_journal_adopted"])
        self.assertEqual(status["tracked_non_generated_journal_count"], 0)
        self.assertEqual(status["valid_tracked_journal_count"], 0)

    def test_adoption_status_fails_closed_when_index_changes_during_validation(
        self,
    ) -> None:
        repo = self.init_repo()
        journal = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-index-race-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Index Race",
            status="active",
            updated="2026-05-05",
        )
        add = run_git(repo, "add", "--", str(journal.relative_to(repo)))
        self.assertEqual(add.returncode, 0, add.stderr)

        original_read = project_journal._read_index_blobs_batch
        mutated = False

        def read_then_mutate(
            repo_arg: pathlib.Path,
            blobs: list[project_journal.IndexJournalBlob],
            *,
            deadline: float | None = None,
        ) -> list[bytes]:
            nonlocal mutated
            contents = original_read(repo_arg, blobs, deadline=deadline)
            if not mutated:
                mutated = True
                journal.write_text(
                    journal.read_text(encoding="utf-8").replace(
                        "status: active",
                        "status: paused",
                    ),
                    encoding="utf-8",
                )
                restage = run_git(
                    repo,
                    "add",
                    "--",
                    str(journal.relative_to(repo)),
                )
                self.assertEqual(restage.returncode, 0, restage.stderr)
            return contents

        with mock.patch.object(
            project_journal,
            "_read_index_blobs_batch",
            side_effect=read_then_mutate,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "Git index changed during validation",
            ):
                project_journal._load_entries_from_index(repo)

    def test_adoption_validation_shares_one_absolute_deadline(self) -> None:
        repo = self.init_repo()

        with mock.patch.object(
            project_journal.time,
            "monotonic",
            return_value=100.0,
        ):
            with mock.patch.object(
                project_journal,
                "_tracked_index_journal_snapshot",
                side_effect=((b"", []), (b"", [])),
            ) as snapshot:
                with mock.patch.object(
                    project_journal,
                    "_read_index_blobs_batch",
                    return_value=[],
                ) as batch:
                    with mock.patch.object(
                        project_journal,
                        "_validate_entries",
                        wraps=project_journal._validate_entries,
                    ) as validate:
                        project_journal._load_entries_from_index(
                            repo,
                            timeout_seconds=2.5,
                        )

        snapshot_deadlines = [
            call.kwargs["deadline"] for call in snapshot.call_args_list
        ]
        self.assertEqual(snapshot_deadlines, [102.5, 102.5])
        self.assertEqual(batch.call_args.kwargs["deadline"], 102.5)
        self.assertEqual(validate.call_args.kwargs["deadline"], 102.5)

    def test_failed_ls_files_reports_stdout_reference_without_echoing_paths(
        self,
    ) -> None:
        repo = self.init_repo()
        raw_stdout = (
            b"100644 "
            + b"a" * 40
            + b" 0\tdocs/project_journal/"
            + b"\xff-secret-path-fragment-" * 1024
            + b".md\0"
        )
        expected_ref = {
            "bytes": len(raw_stdout),
            "sha256": project_journal.hashlib.sha256(raw_stdout).hexdigest(),
        }

        with mock.patch.object(
            project_journal,
            "_capture_bounded_process",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=7,
                stdout=raw_stdout,
                stderr=b"",
            ),
        ):
            with self.assertRaises(project_journal.UserError) as raised:
                project_journal._tracked_index_journal_snapshot(repo)

        message = str(raised.exception)
        self.assertIn(
            "stdout_ref="
            + json.dumps(
                expected_ref,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ),
            message,
        )
        self.assertNotIn(os.fsdecode(raw_stdout), message)
        self.assertNotIn("secret-path-fragment", message)

    def test_adoption_command_starts_deadline_before_repo_resolution(self) -> None:
        repo = self.init_repo().resolve()
        args = mock.Mock(repo=str(repo))
        adoption = {
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }

        with mock.patch.object(
            project_journal.time,
            "monotonic",
            return_value=100.0,
        ):
            with mock.patch.object(
                project_journal,
                "_resolve_repo",
                return_value=repo,
            ) as resolve:
                with mock.patch.object(
                    project_journal,
                    "_tracked_journal_adoption",
                    return_value=adoption,
                ) as tracked:
                    with mock.patch("builtins.print"):
                        project_journal.command_adoption_status(args)

        expected_deadline = (
            100.0 + project_journal.GIT_ADOPTION_VALIDATION_TIMEOUT_SECONDS
        )
        self.assertEqual(resolve.call_args.kwargs["deadline"], expected_deadline)
        self.assertEqual(tracked.call_args.kwargs["deadline"], expected_deadline)
        self.assertEqual(
            resolve.call_args.kwargs["deadline_error"],
            "tracked journal adoption validation exceeded its shared deadline",
        )

    def test_repo_resolution_uses_bounded_capture_with_shared_deadline(self) -> None:
        repo = self.init_repo().resolve()
        completed = subprocess.CompletedProcess(
            [],
            0,
            f"{repo}\n".encode(),
            b"",
        )

        with mock.patch.object(
            project_journal,
            "_capture_bounded_process",
            return_value=completed,
        ) as capture:
            resolved = project_journal._resolve_repo(
                str(repo),
                deadline=123.0,
                deadline_error="shared deadline",
            )

        self.assertEqual(resolved, repo.resolve())
        self.assertEqual(capture.call_args.kwargs["deadline"], 123.0)
        self.assertEqual(capture.call_args.kwargs["timeout_error"], "shared deadline")
        self.assertEqual(
            capture.call_args.kwargs["operation"],
            "Git repository resolution",
        )

    def test_repo_resolution_avoids_synchronous_path_canonicalization(self) -> None:
        repo = self.init_repo().resolve()

        with mock.patch.object(
            pathlib.Path,
            "resolve",
            side_effect=AssertionError("synchronous resolve must not run"),
        ):
            resolved = project_journal._resolve_repo(
                str(repo),
                deadline=time.monotonic() + 5,
                deadline_error="shared deadline",
            )

        self.assertEqual(resolved, repo)

    def test_repo_resolution_preserves_symlink_dotdot_semantics(self) -> None:
        physical = self.root / "physical"
        nested = physical / "nested"
        nested.mkdir(parents=True)
        repo = physical / "repo"
        repo.mkdir()
        initialized = run_git(repo, "init")
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        link = self.root / "linked-nested"
        link.symlink_to(nested, target_is_directory=True)

        resolved = project_journal._resolve_repo(
            str(link / ".." / "repo"),
            deadline=time.monotonic() + 5,
            deadline_error="shared deadline",
        )

        self.assertEqual(resolved, repo.resolve())

    def test_failed_discovery_resolution_returns_none_after_complete_no_marker_scan(
        self,
    ) -> None:
        existing = self.root / "plain"
        existing.mkdir()
        candidate = existing / "missing" / "nested"
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: not a git repository",
        )

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ) as run:
            resolved = project_journal._repo_root_for_existing_path(
                candidate,
                deadline=time.monotonic() + 5,
            )

        self.assertIsNone(resolved)
        self.assertEqual(run.call_args.args[0], existing)

    def test_failed_discovery_resolution_binds_marker_lookup_across_symlink_retarget(
        self,
    ) -> None:
        target_with_marker = self.root / "retarget-a"
        target_without_marker = self.root / "retarget-b"
        target_with_marker.mkdir()
        target_without_marker.mkdir()
        (target_with_marker / ".git").mkdir()
        link = self.root / "retarget-link"
        link.symlink_to(target_with_marker, target_is_directory=True)
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_stat = project_journal.os.stat
        injected = False
        marker_probe_used_descriptor = False
        marker_probe_found_marker = False
        observed_b_during_probe = False
        restored_a_after_probe = False

        def atomically_retarget_link(target: pathlib.Path) -> None:
            replacement = self.root / "retarget-link.next"
            replacement.unlink(missing_ok=True)
            replacement.symlink_to(target, target_is_directory=True)
            os.replace(replacement, link)

        def retarget_during_marker_lookup(
            path: os.PathLike[str] | str,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            nonlocal injected
            nonlocal marker_probe_found_marker
            nonlocal marker_probe_used_descriptor
            nonlocal observed_b_during_probe
            nonlocal restored_a_after_probe
            path_text = os.fspath(path)
            is_marker_lookup = not follow_symlinks and (
                (dir_fd is None and pathlib.Path(path) == link / ".git")
                or (dir_fd is not None and path_text == ".git")
            )
            if is_marker_lookup and not injected:
                marker_probe_used_descriptor = dir_fd is not None
                atomically_retarget_link(target_without_marker)
                observed_b_during_probe = (
                    project_journal._repository_directory_identity(actual_stat(link))
                    == project_journal._repository_directory_identity(
                        actual_stat(target_without_marker)
                    )
                )
                try:
                    result = actual_stat(
                        path,
                        dir_fd=dir_fd,
                        follow_symlinks=follow_symlinks,
                    )
                    marker_probe_found_marker = True
                    return result
                finally:
                    atomically_retarget_link(target_with_marker)
                    restored_a_after_probe = (
                        project_journal._repository_directory_identity(
                            actual_stat(link)
                        )
                        == project_journal._repository_directory_identity(
                            actual_stat(target_with_marker)
                        )
                    )
                    injected = True
            return actual_stat(
                path,
                dir_fd=dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "stat",
                side_effect=retarget_during_marker_lookup,
            ):
                with self.assertRaises(
                    project_journal.RepositoryResolutionError,
                ) as raised:
                    project_journal._repo_root_for_existing_path(
                        link,
                        deadline=time.monotonic() + 5,
                    )

        self.assertTrue(injected)
        self.assertTrue(os.path.samefile(link, target_with_marker))
        self.assertTrue(observed_b_during_probe)
        self.assertTrue(restored_a_after_probe)
        self.assertTrue(marker_probe_used_descriptor)
        self.assertTrue(marker_probe_found_marker)
        self.assertEqual(
            raised.exception.resolution_reason,
            "git_marker_present",
        )
        self.assertEqual(raised.exception.marker_kind, "directory")
        self.assertIsNone(raised.exception.marker_path)
        self.assertEqual(raised.exception.marker_path_hint, link / ".git")
        self.assertEqual(
            raised.exception.marker_path_status,
            "path_unverified",
        )

    def test_failed_discovery_resolution_initial_bind_follows_stable_symlink(
        self,
    ) -> None:
        target = self.root / "stable-symlink-target"
        target.mkdir()
        link = self.root / "stable-symlink"
        link.symlink_to(target, target_is_directory=True)
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_open = project_journal.os.open
        initial_flags: int | None = None
        parent_flags: list[int] = []

        def capture_directory_flags(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal initial_flags
            if pathlib.Path(path) == link and dir_fd is None:
                initial_flags = flags
            if os.fspath(path) == ".." and dir_fd is not None:
                parent_flags.append(flags)
            return actual_open(
                path,
                flags,
                mode,
                dir_fd=dir_fd,
            )

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "open",
                side_effect=capture_directory_flags,
            ):
                resolved = project_journal._repo_root_for_existing_path(
                    link,
                    deadline=time.monotonic() + 5,
                )

        self.assertIsNone(resolved)
        self.assertIsNotNone(initial_flags)
        assert initial_flags is not None
        self.assertTrue(initial_flags & os.O_DIRECTORY)
        self.assertTrue(initial_flags & os.O_CLOEXEC)
        self.assertTrue(initial_flags & os.O_NONBLOCK)
        self.assertFalse(initial_flags & os.O_NOFOLLOW)
        self.assertTrue(parent_flags)
        for flags in parent_flags:
            self.assertTrue(flags & os.O_DIRECTORY)
            self.assertTrue(flags & os.O_CLOEXEC)
            self.assertTrue(flags & os.O_NONBLOCK)
            self.assertTrue(flags & os.O_NOFOLLOW)

    def test_failed_discovery_resolution_initial_bind_mismatch_preserves_close_error(
        self,
    ) -> None:
        target_a = self.root / "initial-bind-a"
        target_b = self.root / "initial-bind-b"
        target_a.mkdir()
        target_b.mkdir()
        link = self.root / "initial-bind-link"
        link.symlink_to(target_a, target_is_directory=True)
        retargeted = False
        actual_close = project_journal.os.close
        close_calls: list[int] = []

        def retarget_after_pre_git_stat(
            *_args: object,
            **_kwargs: object,
        ) -> subprocess.CompletedProcess[str]:
            nonlocal retargeted
            link.unlink()
            link.symlink_to(target_b, target_is_directory=True)
            retargeted = True
            return subprocess.CompletedProcess(
                [],
                128,
                "",
                "fatal: injected Git failure",
            )

        def close_then_fail(fd: int) -> None:
            close_calls.append(fd)
            actual_close(fd)
            raise OSError(
                errno.EIO,
                "injected initial descriptor close failure",
            )

        with mock.patch.object(
            project_journal,
            "_run_git",
            side_effect=retarget_after_pre_git_stat,
        ):
            with mock.patch.object(
                project_journal.os,
                "close",
                side_effect=close_then_fail,
            ):
                with self.assertRaises(
                    project_journal.RepositoryResolutionError,
                ) as raised:
                    project_journal._repo_root_for_existing_path(
                        link,
                        deadline=time.monotonic() + 5,
                    )

        self.assertTrue(retargeted)
        self.assertEqual(len(close_calls), 1)
        self.assertEqual(
            raised.exception.resolution_reason,
            "ancestor_changed",
        )
        self.assertEqual(raised.exception.path_status, "path_unverified")
        self.assertEqual(len(raised.exception.cleanup_errors), 1)
        self.assertEqual(raised.exception.cleanup_errors[0]["errno"], errno.EIO)
        serialized = project_journal._discovery_error(raised.exception)
        self.assertEqual(serialized["path_status"], "path_unverified")
        self.assertEqual(serialized["cleanup_errors"][0]["errno"], errno.EIO)

    def test_failed_discovery_resolution_never_returns_none_after_close_failure(
        self,
    ) -> None:
        cwd = self.root / "close-failure-candidate"
        cwd.mkdir()
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_open = project_journal.os.open
        actual_close = project_journal.os.close
        opened_fds: set[int] = set()
        close_calls: list[int] = []
        close_failure_count = 0

        def track_open(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            result = actual_open(
                path,
                flags,
                mode,
                dir_fd=dir_fd,
            )
            opened_fds.add(result)
            return result

        def close_then_fail_twice(fd: int) -> None:
            nonlocal close_failure_count
            close_calls.append(fd)
            actual_close(fd)
            close_failure_count += 1
            if close_failure_count <= 2:
                raise OSError(
                    errno.EIO,
                    f"injected descriptor close failure {close_failure_count}",
                )

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "open",
                side_effect=track_open,
            ):
                with mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=close_then_fail_twice,
                ):
                    with self.assertRaises(
                        project_journal.RepositoryResolutionError,
                    ) as raised:
                        project_journal._repo_root_for_existing_path(
                            cwd,
                            deadline=time.monotonic() + 5,
                        )

        self.assertEqual(
            raised.exception.resolution_reason,
            "descriptor_close_failed",
        )
        self.assertEqual(set(close_calls), opened_fds)
        self.assertEqual(len(close_calls), len(opened_fds))
        self.assertEqual(len(raised.exception.cleanup_errors), 1)
        self.assertEqual(
            project_journal._discovery_error(raised.exception)["cleanup_errors"][0][
                "errno"
            ],
            errno.EIO,
        )

    def test_failed_discovery_resolution_preserves_marker_primary_on_close_failure(
        self,
    ) -> None:
        cwd = self.root / "marker-close-failure"
        cwd.mkdir()
        (cwd / ".git").mkdir()
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_close = project_journal.os.close
        close_calls: list[int] = []

        def close_marker_descriptor_then_fail(fd: int) -> None:
            close_calls.append(fd)
            actual_close(fd)
            raise OSError(
                errno.EIO,
                "injected marker descriptor close failure",
            )

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "close",
                side_effect=close_marker_descriptor_then_fail,
            ):
                with self.assertRaises(
                    project_journal.RepositoryResolutionError,
                ) as raised:
                    project_journal._repo_root_for_existing_path(
                        cwd,
                        deadline=time.monotonic() + 5,
                    )

        self.assertEqual(len(close_calls), 1)
        self.assertEqual(
            raised.exception.resolution_reason,
            "git_marker_present",
        )
        self.assertEqual(len(raised.exception.cleanup_errors), 1)
        self.assertEqual(raised.exception.cleanup_errors[0]["errno"], errno.EIO)

    def test_failed_discovery_resolution_preserves_close_base_exception(
        self,
    ) -> None:
        cwd = self.root / "close-interrupt-candidate"
        cwd.mkdir()
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_close = project_journal.os.close
        interruption = LegacyInterrupt("injected descriptor close interruption")
        interrupted = False

        def close_then_interrupt_once(fd: int) -> None:
            nonlocal interrupted
            actual_close(fd)
            if not interrupted:
                interrupted = True
                raise interruption

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "close",
                side_effect=close_then_interrupt_once,
            ):
                with self.assertRaises(LegacyInterrupt) as raised:
                    project_journal._repo_root_for_existing_path(
                        cwd,
                        deadline=time.monotonic() + 5,
                    )

        self.assertTrue(interrupted)
        self.assertIs(raised.exception, interruption)

    def test_failed_discovery_resolution_async_handoff_uses_one_fd_owner(
        self,
    ) -> None:
        classify = project_journal._classify_failed_git_resolution
        source_lines, first_line = inspect.getsourcelines(classify)

        def exact_line_number(source: str) -> int:
            matches = [
                first_line + offset
                for offset, line in enumerate(source_lines)
                if line.strip() == source
            ]
            self.assertEqual(len(matches), 1)
            return matches[0]

        target_lines = {
            "before_handoff_close": exact_line_number("owner.close("),
            "after_parent_promotion": exact_line_number("parent_fd = None"),
            "terminal_return": exact_line_number("return None"),
        }
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_open = project_journal.os.open
        actual_close = project_journal.os.close
        owner_type = project_journal._RepositoryResolutionFdOwner

        for phase, target_line in target_lines.items():
            with self.subTest(phase=phase):
                cwd = self.root / phase / "a" / "b"
                cwd.mkdir(parents=True)
                opened_fds: list[int] = []
                close_calls: list[int] = []
                owners: list[project_journal._RepositoryResolutionFdOwner] = []
                interruption = LegacyInterrupt(
                    f"injected {phase} descriptor-owner interruption"
                )
                injected = False

                def track_open(
                    path: os.PathLike[str] | str,
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    result = actual_open(
                        path,
                        flags,
                        mode,
                        dir_fd=dir_fd,
                    )
                    opened_fds.append(result)
                    return result

                def track_close(fd: int) -> None:
                    close_calls.append(fd)
                    actual_close(fd)

                def capture_owner(
                    source_path: pathlib.Path,
                ) -> project_journal._RepositoryResolutionFdOwner:
                    owner = owner_type(source_path)
                    owners.append(owner)
                    return owner

                def interrupt_at_target(
                    frame: object,
                    event: str,
                    _arg: object,
                ) -> object:
                    nonlocal injected
                    if (
                        not injected
                        and event == "line"
                        and getattr(frame, "f_code", None) is classify.__code__
                        and getattr(frame, "f_lineno", None) == target_line
                    ):
                        injected = True
                        raise interruption
                    return interrupt_at_target

                previous_trace = sys.gettrace()
                try:
                    with mock.patch.object(
                        project_journal,
                        "_run_git",
                        return_value=failure,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_RepositoryResolutionFdOwner",
                            side_effect=capture_owner,
                        ):
                            with mock.patch.object(
                                project_journal.os,
                                "open",
                                side_effect=track_open,
                            ):
                                with mock.patch.object(
                                    project_journal.os,
                                    "close",
                                    side_effect=track_close,
                                ):
                                    sys.settrace(interrupt_at_target)
                                    with self.assertRaises(
                                        LegacyInterrupt,
                                    ) as raised:
                                        project_journal._repo_root_for_existing_path(
                                            cwd,
                                            deadline=time.monotonic() + 5,
                                        )
                finally:
                    sys.settrace(previous_trace)

                self.assertTrue(injected)
                self.assertIs(raised.exception, interruption)
                self.assertTrue(opened_fds)
                self.assertCountEqual(close_calls, opened_fds)
                self.assertEqual(len(close_calls), len(opened_fds))
                self.assertEqual(len(owners), 1)
                self.assertEqual(owners[0].owned_fds, ())

    def test_failed_discovery_resolution_owner_drain_preserves_active_primary(
        self,
    ) -> None:
        owner_type = project_journal._RepositoryResolutionFdOwner

        def exact_line_number(function: object, source: str) -> int:
            source_lines, first_line = inspect.getsourcelines(function)
            matches = [
                first_line + offset
                for offset, line in enumerate(source_lines)
                if line.strip() == source
            ]
            self.assertEqual(len(matches), 1)
            return matches[0]

        target_lines = {
            "before_context_drain": (
                owner_type.__exit__,
                exact_line_number(
                    owner_type.__exit__,
                    "cleanup_error, cleanup_cause = "
                    "self.close_all(primary=active_error)",
                ),
            ),
            "after_context_drain": (
                owner_type.__exit__,
                exact_line_number(
                    owner_type.__exit__,
                    "if active_error is None and cleanup_error is not None:",
                ),
            ),
            "before_drain_selection": (
                owner_type.close_all,
                exact_line_number(
                    owner_type.close_all,
                    "for fd in tuple(self._owned):",
                ),
            ),
        }
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_open = project_journal.os.open
        actual_close = project_journal.os.close

        for phase, (target_function, target_line) in target_lines.items():
            with self.subTest(phase=phase):
                cwd = self.root / f"owner-drain-{phase}"
                cwd.mkdir()
                primary = LegacyInterrupt(
                    f"injected {phase} active classification primary"
                )
                cleanup_interrupt = LegacyInterrupt(
                    f"injected {phase} owner-drain interruption"
                )
                opened_fds: list[int] = []
                close_calls: list[int] = []
                owners: list[project_journal._RepositoryResolutionFdOwner] = []
                injected = False

                def track_open(
                    path: os.PathLike[str] | str,
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    result = actual_open(
                        path,
                        flags,
                        mode,
                        dir_fd=dir_fd,
                    )
                    opened_fds.append(result)
                    return result

                def track_close(fd: int) -> None:
                    close_calls.append(fd)
                    actual_close(fd)

                def capture_owner(
                    source_path: pathlib.Path,
                ) -> project_journal._RepositoryResolutionFdOwner:
                    owner = owner_type(source_path)
                    owners.append(owner)
                    return owner

                def interrupt_at_target(
                    frame: object,
                    event: str,
                    _arg: object,
                ) -> object:
                    nonlocal injected
                    if (
                        not injected
                        and event == "line"
                        and getattr(frame, "f_code", None) is target_function.__code__
                        and getattr(frame, "f_lineno", None) == target_line
                    ):
                        injected = True
                        raise cleanup_interrupt
                    return interrupt_at_target

                previous_trace = sys.gettrace()
                try:
                    with mock.patch.object(
                        project_journal,
                        "_run_git",
                        return_value=failure,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_RepositoryResolutionFdOwner",
                            side_effect=capture_owner,
                        ):
                            with mock.patch.object(
                                project_journal,
                                "_stat_failed_git_marker",
                                side_effect=primary,
                            ):
                                with mock.patch.object(
                                    project_journal.os,
                                    "open",
                                    side_effect=track_open,
                                ):
                                    with mock.patch.object(
                                        project_journal.os,
                                        "close",
                                        side_effect=track_close,
                                    ):
                                        sys.settrace(interrupt_at_target)
                                        with self.assertRaises(
                                            LegacyInterrupt,
                                        ) as raised:
                                            project_journal._repo_root_for_existing_path(
                                                cwd,
                                                deadline=time.monotonic() + 5,
                                            )
                finally:
                    sys.settrace(previous_trace)

                self.assertTrue(injected)
                self.assertIs(raised.exception, primary)
                self.assertTrue(opened_fds)
                self.assertCountEqual(close_calls, opened_fds)
                self.assertEqual(len(close_calls), len(opened_fds))
                self.assertEqual(len(owners), 1)
                self.assertEqual(owners[0].owned_fds, ())
                cleanup_errors = getattr(primary, "cleanup_errors", [])
                self.assertEqual(len(cleanup_errors), 1)
                self.assertIn(
                    str(cleanup_interrupt),
                    cleanup_errors[0]["message"],
                )

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_repository_resolution_owner_signal_fence_covers_handoff_phases(
        self,
    ) -> None:
        owner_type = project_journal._RepositoryResolutionFdOwner
        target_lines = {
            "before_signal_fence": self.exact_source_line(
                owner_type._close_owned_fd,
                "signal_fence = _block_fd_close_signals()",
            ),
            "after_close_commit": self.exact_source_line(
                owner_type._close_owned_fd,
                "os.close(fd)",
            ),
            "after_close_success": self.exact_source_line(
                owner_type._close_owned_fd,
                "self._close_recovery_eligible = False",
            ),
        }
        actual_close = project_journal.os.close
        flags = project_journal._repository_initial_directory_open_flags(self.root)

        for phase, target_line in target_lines.items():
            with self.subTest(phase=phase):
                owner = owner_type(self.root)
                fd = owner.open(
                    self.root,
                    flags,
                    cleanup_context=f"{phase} descriptor cleanup failed",
                )
                source_stat = os.fstat(fd)
                source_identity = (source_stat.st_dev, source_stat.st_ino)
                source_close_count = 0
                injected = False

                def track_close(candidate_fd: int) -> None:
                    nonlocal source_close_count
                    descriptor_stat = os.fstat(candidate_fd)
                    descriptor_identity = (
                        descriptor_stat.st_dev,
                        descriptor_stat.st_ino,
                    )
                    if descriptor_identity == source_identity:
                        source_close_count += 1
                    actual_close(candidate_fd)

                def send_sigint_at_target(
                    frame: object,
                    event: str,
                    _arg: object,
                ) -> object:
                    nonlocal injected
                    if (
                        not injected
                        and event == "line"
                        and getattr(frame, "f_code", None)
                        is owner_type._close_owned_fd.__code__
                        and getattr(frame, "f_lineno", None) == target_line
                    ):
                        injected = True
                        os.kill(os.getpid(), signal.SIGINT)
                    return send_sigint_at_target

                previous_trace = sys.gettrace()
                try:
                    with self.default_unblocked_sigint():
                        with mock.patch.object(
                            project_journal.os,
                            "close",
                            side_effect=track_close,
                        ):
                            sys.settrace(send_sigint_at_target)
                            with self.assertRaises(KeyboardInterrupt) as raised:
                                owner.close(
                                    fd,
                                    context=f"{phase} descriptor close failed",
                                )
                            sys.settrace(previous_trace)
                            if phase == "before_signal_fence":
                                self.assertEqual(owner.owned_fds, (fd,))
                            owner.close_all(primary=raised.exception)
                finally:
                    sys.settrace(previous_trace)
                    if owner.owned_fds:
                        owner.close_all()

                self.assertTrue(injected)
                self.assertEqual(source_close_count, 1)
                self.assertEqual(owner.owned_fds, ())

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_repository_resolution_owner_signal_fence_does_not_close_reused_fd(
        self,
    ) -> None:
        owner = project_journal._RepositoryResolutionFdOwner(self.root)
        flags = project_journal._repository_initial_directory_open_flags(self.root)
        fd = owner.open(
            self.root,
            flags,
            cleanup_context="reused descriptor cleanup failed",
        )
        source_stat = os.fstat(fd)
        source_identity = (source_stat.st_dev, source_stat.st_ino)
        actual_close = project_journal.os.close
        source_closed = False
        reused_fd: int | None = None

        def close_source_then_reuse(candidate_fd: int) -> None:
            nonlocal reused_fd, source_closed
            descriptor_stat = os.fstat(candidate_fd)
            descriptor_identity = (
                descriptor_stat.st_dev,
                descriptor_stat.st_ino,
            )
            if descriptor_identity == source_identity and not source_closed:
                source_closed = True
                actual_close(candidate_fd)
                replacement = os.open(os.devnull, os.O_RDONLY)
                if replacement != candidate_fd:
                    os.dup2(replacement, candidate_fd)
                    actual_close(replacement)
                reused_fd = candidate_fd
                os.kill(os.getpid(), signal.SIGINT)
                return
            actual_close(candidate_fd)

        try:
            with self.default_unblocked_sigint():
                with mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=close_source_then_reuse,
                ):
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        owner.close(
                            fd,
                            context="reused descriptor close failed",
                        )
                    owner.close_all(primary=raised.exception)

            self.assertTrue(source_closed)
            self.assertEqual(owner.owned_fds, ())
            self.assertIsNotNone(reused_fd)
            assert reused_fd is not None
            os.fstat(reused_fd)
        finally:
            if owner.owned_fds:
                owner.close_all()
            if reused_fd is not None:
                try:
                    actual_close(reused_fd)
                except OSError:
                    pass

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_repository_resolution_owner_preserves_close_error_over_pending_sigint(
        self,
    ) -> None:
        owner = project_journal._RepositoryResolutionFdOwner(self.root)
        flags = project_journal._repository_initial_directory_open_flags(self.root)
        fd = owner.open(
            self.root,
            flags,
            cleanup_context="dual failure descriptor cleanup failed",
        )
        actual_close = project_journal.os.close
        close_count = 0
        close_error = OSError(
            errno.EIO,
            "injected owner close failure before pending SIGINT restore",
        )

        def close_then_fail_with_pending_sigint(candidate_fd: int) -> None:
            nonlocal close_count
            self.assertEqual(candidate_fd, fd)
            close_count += 1
            actual_close(candidate_fd)
            os.kill(os.getpid(), signal.SIGINT)
            raise close_error

        try:
            with self.default_unblocked_sigint():
                with mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=close_then_fail_with_pending_sigint,
                ):
                    with self.assertRaises(
                        project_journal.RepositoryResolutionError,
                    ) as raised:
                        owner.close(
                            fd,
                            context="dual failure descriptor close failed",
                        )

            self.assertIs(raised.exception.__cause__, close_error)
            self.assertEqual(raised.exception.errno, errno.EIO)
            self.assertEqual(close_count, 1)
            self.assertEqual(owner.owned_fds, ())
            with self.assertRaises(OSError) as closed:
                os.fstat(fd)
            self.assertEqual(closed.exception.errno, errno.EBADF)
            notes = "\n".join(getattr(raised.exception, "__notes__", ()))
            self.assertIn("type=KeyboardInterrupt", notes)
            self.assertEqual(notes.count("type=KeyboardInterrupt"), 1)
        finally:
            if owner.owned_fds:
                owner.close_all()

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_repository_resolution_owner_dual_close_restore_failure_is_secondary_to_active_error(
        self,
    ) -> None:
        owner = project_journal._RepositoryResolutionFdOwner(self.root)
        flags = project_journal._repository_initial_directory_open_flags(self.root)
        actual_close = project_journal.os.close
        active_error = LegacyInterrupt(
            "injected active repository-resolution interruption"
        )
        close_error = OSError(
            errno.EIO,
            "injected secondary owner close failure",
        )
        opened_fd: int | None = None
        close_count = 0

        def close_then_fail_with_pending_sigint(candidate_fd: int) -> None:
            nonlocal close_count
            self.assertEqual(candidate_fd, opened_fd)
            close_count += 1
            actual_close(candidate_fd)
            os.kill(os.getpid(), signal.SIGINT)
            raise close_error

        try:
            with self.default_unblocked_sigint():
                with mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=close_then_fail_with_pending_sigint,
                ):
                    with self.assertRaises(LegacyInterrupt) as raised:
                        with owner:
                            opened_fd = owner.open(
                                self.root,
                                flags,
                                cleanup_context=(
                                    "active owner descriptor cleanup failed"
                                ),
                            )
                            raise active_error

            self.assertIs(raised.exception, active_error)
            self.assertEqual(close_count, 1)
            self.assertEqual(owner.owned_fds, ())
            self.assertIsNotNone(opened_fd)
            assert opened_fd is not None
            with self.assertRaises(OSError) as closed:
                os.fstat(opened_fd)
            self.assertEqual(closed.exception.errno, errno.EBADF)
            cleanup_errors = getattr(active_error, "cleanup_errors", ())
            self.assertEqual(len(cleanup_errors), 1)
            cleanup = cleanup_errors[0]
            self.assertEqual(cleanup["error_type"], "OSError")
            self.assertEqual(cleanup["errno"], errno.EIO)
            self.assertEqual(cleanup["error_name"], "EIO")
            self.assertIn(
                "type=KeyboardInterrupt",
                "\n".join(cleanup["details"]),
            )
            notes = "\n".join(getattr(active_error, "__notes__", ()))
            self.assertIn("injected secondary owner close failure", notes)
            self.assertIn("type=KeyboardInterrupt", notes)
        finally:
            if owner.owned_fds:
                owner.close_all()

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_repository_resolution_owner_transient_mask_failure_uses_attempt_time_evidence(
        self,
    ) -> None:
        owner = project_journal._RepositoryResolutionFdOwner(self.root)
        flags = project_journal._repository_initial_directory_open_flags(self.root)
        actual_block = project_journal._block_fd_close_signals
        mask_failure = project_journal.UnsupportedPlatform(
            "injected transient signal-mask acquisition failure"
        )
        block_calls = 0
        opened_fd: int | None = None

        def fail_once_then_block() -> project_journal._FdCloseSignalFence:
            nonlocal block_calls
            block_calls += 1
            if block_calls == 1:
                raise mask_failure
            return actual_block()

        with mock.patch.object(
            project_journal,
            "_block_fd_close_signals",
            side_effect=fail_once_then_block,
        ):
            with self.assertRaises(
                project_journal.RepositoryResolutionError,
            ) as raised:
                with owner:
                    opened_fd = owner.open(
                        self.root,
                        flags,
                        cleanup_context="transient mask descriptor cleanup failed",
                    )

        self.assertEqual(block_calls, 2)
        self.assertIs(raised.exception.__cause__, mask_failure)
        self.assertEqual(owner.owned_fds, ())
        self.assertNotIn("descriptor remains owned", str(raised.exception))
        self.assertIn(
            "close could not begin during this cleanup attempt",
            str(raised.exception),
        )
        self.assertNotIn(
            "final context-exit drain incomplete",
            "\n".join(getattr(raised.exception, "__notes__", ())),
        )
        self.assertIsNotNone(opened_fd)
        assert opened_fd is not None
        with self.assertRaises(OSError) as closed:
            os.fstat(opened_fd)
        self.assertEqual(closed.exception.errno, errno.EBADF)

    @unittest.skipUnless(os.name == "posix", "POSIX signal-mask contract")
    def test_repository_resolution_owner_persistent_context_exit_records_final_boundary(
        self,
    ) -> None:
        owner = project_journal._RepositoryResolutionFdOwner(self.root)
        flags = project_journal._repository_initial_directory_open_flags(self.root)
        failures: list[project_journal.UnsupportedPlatform] = []
        opened_fd: int | None = None

        def reject_signal_mask() -> project_journal._FdCloseSignalFence:
            failure = project_journal.UnsupportedPlatform(
                "injected persistent context-exit signal-mask failure"
            )
            failures.append(failure)
            raise failure

        try:
            with mock.patch.object(
                project_journal,
                "_block_fd_close_signals",
                side_effect=reject_signal_mask,
            ):
                with self.assertRaises(
                    project_journal.RepositoryResolutionError,
                ) as raised:
                    with owner:
                        opened_fd = owner.open(
                            self.root,
                            flags,
                            cleanup_context=(
                                "persistent context-exit descriptor cleanup failed"
                            ),
                        )

            self.assertEqual(len(failures), 2)
            self.assertIs(raised.exception.__cause__, failures[0])
            self.assertIsNotNone(opened_fd)
            assert opened_fd is not None
            self.assertEqual(owner.owned_fds, (opened_fd,))
            os.fstat(opened_fd)
            notes = getattr(raised.exception, "__notes__", ())
            final_boundary = (
                "repository-resolution descriptor owner final context-exit drain "
                "incomplete; 1 descriptors remained owned at that boundary"
            )
            self.assertEqual(notes.count(final_boundary), 1)
        finally:
            if owner.owned_fds:
                owner.close_all()

    def test_repository_resolution_owner_persistent_mask_failure_is_bounded(
        self,
    ) -> None:
        owner = project_journal._RepositoryResolutionFdOwner(self.root)
        flags = project_journal._repository_initial_directory_open_flags(self.root)
        opened_fds = [
            owner.open(
                self.root,
                flags,
                cleanup_context=f"descriptor {index} cleanup failed",
            )
            for index in range(2)
        ]
        failures: list[project_journal.UnsupportedPlatform] = []

        def reject_signal_mask() -> project_journal._FdCloseSignalFence:
            failure = project_journal.UnsupportedPlatform(
                "injected persistent signal-mask acquisition failure"
            )
            if not failures:
                project_journal._add_exception_detail(
                    failure,
                    "injected bounded acquisition detail",
                )
            failures.append(failure)
            raise failure

        try:
            with mock.patch.object(
                project_journal,
                "_block_fd_close_signals",
                side_effect=reject_signal_mask,
            ):
                cleanup_error, cleanup_cause = owner.close_all()

            self.assertEqual(len(failures), len(opened_fds))
            self.assertIsNotNone(cleanup_error)
            assert cleanup_error is not None
            self.assertIs(cleanup_cause, failures[0])
            self.assertEqual(
                cleanup_error.resolution_reason,
                "descriptor_close_failed",
            )
            self.assertNotIn("descriptor remains owned", str(cleanup_error))
            self.assertIn(
                "close could not begin during this cleanup attempt",
                str(cleanup_error),
            )
            cleanup_notes = getattr(cleanup_error, "__notes__", ())
            self.assertEqual(
                sum(
                    "injected bounded acquisition detail" in note
                    for note in cleanup_notes
                ),
                1,
            )
            self.assertCountEqual(owner.owned_fds, opened_fds)
            for fd in opened_fds:
                os.fstat(fd)

            owner.close_all(primary=cleanup_error)
            self.assertEqual(owner.owned_fds, ())
        finally:
            if owner.owned_fds:
                owner.close_all()

    def test_repository_resolution_owner_mask_restore_failure_retires_fd(
        self,
    ) -> None:
        owner = project_journal._RepositoryResolutionFdOwner(self.root)
        flags = project_journal._repository_initial_directory_open_flags(self.root)
        opened_fds = [
            owner.open(
                self.root,
                flags,
                cleanup_context=f"descriptor {index} cleanup failed",
            )
            for index in range(2)
        ]
        restore_failure = project_journal.UnsupportedPlatform(
            "injected persistent signal-mask restoration failure"
        )
        signal_fence = mock.Mock(spec=project_journal._FdCloseSignalFence)
        signal_fence.restore.side_effect = restore_failure

        with mock.patch.object(
            project_journal,
            "_block_fd_close_signals",
            return_value=signal_fence,
        ):
            cleanup_error, cleanup_cause = owner.close_all()

        self.assertIsNotNone(cleanup_error)
        assert cleanup_error is not None
        self.assertIs(cleanup_cause, restore_failure)
        self.assertEqual(
            cleanup_error.resolution_reason,
            "descriptor_close_failed",
        )
        self.assertEqual(signal_fence.restore.call_count, len(opened_fds))
        self.assertEqual(owner.owned_fds, ())
        for fd in opened_fds:
            with self.assertRaises(OSError) as raised:
                os.fstat(fd)
            self.assertEqual(raised.exception.errno, errno.EBADF)

    def test_repository_resolution_owner_preserves_first_close_failure_across_drain_interrupt(
        self,
    ) -> None:
        owner_type = project_journal._RepositoryResolutionFdOwner
        source_lines, first_line = inspect.getsourcelines(owner_type.close_all)
        target_sources = {
            "before_cleanup_wrapper": "cleanup_primary = RepositoryResolutionError(",
            "before_cleanup_persist": (
                "self._cleanup_failure = (cleanup_primary, cleanup_error)"
            ),
            "after_cleanup_persist": ("for fd in tuple(self._owned):"),
        }
        target_lines: dict[str, int] = {}
        for phase, target_source in target_sources.items():
            matches = [
                first_line + offset
                for offset, line in enumerate(source_lines)
                if line.strip() == target_source
            ]
            self.assertEqual(len(matches), 1)
            target_lines[phase] = matches[0]
        actual_open = project_journal.os.open
        actual_close = project_journal.os.close
        flags = project_journal._repository_initial_directory_open_flags(self.root)

        for phase, target_line in target_lines.items():
            with self.subTest(phase=phase):
                opened_fds: list[int] = []
                close_calls: list[int] = []
                close_failure: OSError | None = None
                drain_interrupt = LegacyInterrupt(
                    f"injected {phase} interruption after close failure"
                )
                injected = False
                owner = owner_type(self.root)

                def track_open(
                    path: os.PathLike[str] | str,
                    open_flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    result = actual_open(
                        path,
                        open_flags,
                        mode,
                        dir_fd=dir_fd,
                    )
                    opened_fds.append(result)
                    return result

                def close_then_fail_once(fd: int) -> None:
                    nonlocal close_failure
                    close_calls.append(fd)
                    actual_close(fd)
                    if close_failure is None:
                        close_failure = OSError(
                            errno.EIO,
                            "injected first descriptor close failure",
                        )
                        project_journal._add_exception_detail(
                            close_failure,
                            "injected recovered close source detail",
                        )
                        raise close_failure

                def interrupt_cleanup_state(
                    frame: object,
                    event: str,
                    _arg: object,
                ) -> object:
                    nonlocal injected
                    if (
                        close_failure is not None
                        and not injected
                        and event == "line"
                        and getattr(frame, "f_code", None)
                        is owner_type.close_all.__code__
                        and getattr(frame, "f_lineno", None) == target_line
                    ):
                        injected = True
                        raise drain_interrupt
                    return interrupt_cleanup_state

                previous_trace = sys.gettrace()
                try:
                    with mock.patch.object(
                        project_journal.os,
                        "open",
                        side_effect=track_open,
                    ):
                        with mock.patch.object(
                            project_journal.os,
                            "close",
                            side_effect=close_then_fail_once,
                        ):
                            sys.settrace(interrupt_cleanup_state)
                            with self.assertRaises(
                                project_journal.RepositoryResolutionError,
                            ) as raised:
                                with owner:
                                    owner.open(
                                        self.root,
                                        flags,
                                        cleanup_context=(
                                            "first descriptor cleanup failed"
                                        ),
                                    )
                                    owner.open(
                                        self.root,
                                        flags,
                                        cleanup_context=(
                                            "second descriptor cleanup failed"
                                        ),
                                    )
                finally:
                    sys.settrace(previous_trace)

                self.assertTrue(injected)
                self.assertIsNotNone(close_failure)
                self.assertIs(raised.exception.__cause__, close_failure)
                self.assertEqual(
                    raised.exception.resolution_reason,
                    "descriptor_close_failed",
                )
                self.assertEqual(raised.exception.errno, errno.EIO)
                self.assertCountEqual(close_calls, opened_fds)
                self.assertEqual(len(close_calls), len(opened_fds))
                self.assertEqual(owner.owned_fds, ())
                notes = getattr(raised.exception, "__notes__", ())
                self.assertEqual(
                    sum(
                        "injected recovered close source detail" in note
                        for note in notes
                    ),
                    1,
                )
                self.assertLessEqual(
                    len(notes),
                    project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS,
                )
                self.assertEqual(len(raised.exception.cleanup_errors), 1)
                self.assertIn(
                    str(drain_interrupt),
                    raised.exception.cleanup_errors[0]["message"],
                )

    def test_repository_resolution_owner_does_not_treat_ambient_context_as_close_failure(
        self,
    ) -> None:
        owner_type = project_journal._RepositoryResolutionFdOwner
        source_lines, first_line = inspect.getsourcelines(owner_type.__exit__)
        drain_lines = [
            first_line + offset
            for offset, line in enumerate(source_lines)
            if line.strip() == "cleanup_error, cleanup_cause = "
            "self.close_all(primary=active_error)"
        ]
        self.assertEqual(len(drain_lines), 1)
        drain_line = drain_lines[0]
        actual_open = project_journal.os.open
        actual_close = project_journal.os.close
        opened_fds: list[int] = []
        close_calls: list[int] = []
        ambient = project_journal.UnsupportedGitVersion(
            "injected ambient Git version failure"
        )
        interruption = LegacyInterrupt(
            "injected context-exit interruption before any close"
        )
        injected = False
        owner = owner_type(self.root)
        flags = project_journal._repository_initial_directory_open_flags(self.root)

        def track_open(
            path: os.PathLike[str] | str,
            open_flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            result = actual_open(
                path,
                open_flags,
                mode,
                dir_fd=dir_fd,
            )
            opened_fds.append(result)
            return result

        def track_close(fd: int) -> None:
            close_calls.append(fd)
            actual_close(fd)

        def interrupt_before_drain(
            frame: object,
            event: str,
            _arg: object,
        ) -> object:
            nonlocal injected
            if (
                not injected
                and event == "line"
                and getattr(frame, "f_code", None) is owner_type.__exit__.__code__
                and getattr(frame, "f_lineno", None) == drain_line
            ):
                injected = True
                raise interruption
            return interrupt_before_drain

        previous_trace = sys.gettrace()
        try:
            raise ambient
        except project_journal.UnsupportedGitVersion:
            try:
                with mock.patch.object(
                    project_journal.os,
                    "open",
                    side_effect=track_open,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "close",
                        side_effect=track_close,
                    ):
                        sys.settrace(interrupt_before_drain)
                        with self.assertRaises(LegacyInterrupt) as raised:
                            with owner:
                                owner.open(
                                    self.root,
                                    flags,
                                    cleanup_context="ambient descriptor cleanup failed",
                                )
            finally:
                sys.settrace(previous_trace)

        self.assertTrue(injected)
        self.assertIs(raised.exception, interruption)
        self.assertIs(interruption.__context__, ambient)
        self.assertCountEqual(close_calls, opened_fds)
        self.assertEqual(len(close_calls), len(opened_fds))
        self.assertEqual(owner.owned_fds, ())

    def test_failed_discovery_resolution_fails_closed_without_required_flags(
        self,
    ) -> None:
        cwd = self.root / "missing-descriptor-flag"
        cwd.mkdir()
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_required_flag = project_journal._required_open_flag

        def reject_cloexec(name: str) -> int:
            if name == "O_CLOEXEC":
                raise project_journal.UnsupportedPlatform("injected missing O_CLOEXEC")
            return actual_required_flag(name)

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal,
                "_required_open_flag",
                side_effect=reject_cloexec,
            ):
                with mock.patch.object(
                    project_journal.os,
                    "open",
                ) as opened:
                    with self.assertRaises(
                        project_journal.RepositoryResolutionError,
                    ) as raised:
                        project_journal._repo_root_for_existing_path(
                            cwd,
                            deadline=time.monotonic() + 5,
                        )

        opened.assert_not_called()
        self.assertEqual(
            raised.exception.resolution_reason,
            "descriptor_traversal_unavailable",
        )

    def test_failed_discovery_resolution_classifies_every_git_marker_type(
        self,
    ) -> None:
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        for marker_kind, expected_token, expected_label in (
            ("directory", "directory", "directory"),
            ("regular", "regular_file", "regular file"),
            ("symlink", "symlink", "symlink"),
            ("fifo", "other", "other filesystem object"),
        ):
            with self.subTest(marker_kind=marker_kind):
                root = self.root / f"marker-{marker_kind}"
                cwd = root / "nested"
                cwd.mkdir(parents=True)
                marker = root / ".git"
                if marker_kind == "directory":
                    marker.mkdir()
                elif marker_kind == "regular":
                    marker.write_text(
                        "gitdir: ../private-git-dir\n",
                        encoding="utf-8",
                    )
                elif marker_kind == "symlink":
                    marker.symlink_to(self.root / "missing-marker-target")
                else:
                    os.mkfifo(marker)
                observed_marker = cwd / ".." / ".git"
                actual_open = project_journal.os.open
                opened_paths: list[str] = []

                def reject_marker_open(
                    path: os.PathLike[str] | str,
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    path_text = os.fspath(path)
                    opened_paths.append(path_text)
                    if pathlib.PurePath(path_text).name == ".git":
                        raise AssertionError(
                            ".git markers must not be opened or parsed"
                        )
                    return actual_open(
                        path,
                        flags,
                        mode,
                        dir_fd=dir_fd,
                    )

                with mock.patch.object(
                    project_journal,
                    "_run_git",
                    return_value=failure,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "open",
                        side_effect=reject_marker_open,
                    ):
                        with self.assertRaises(
                            project_journal.RepositoryResolutionError,
                        ) as raised:
                            project_journal._repo_root_for_existing_path(
                                cwd,
                                deadline=time.monotonic() + 5,
                            )

                self.assertTrue(opened_paths)
                self.assertNotIn(".git", opened_paths)
                self.assertEqual(
                    raised.exception.code,
                    "repository_resolution_failed",
                )
                self.assertEqual(
                    raised.exception.resolution_reason,
                    "git_marker_present",
                )
                self.assertEqual(
                    raised.exception.marker_kind,
                    expected_token,
                )
                self.assertIsNone(raised.exception.marker_path)
                self.assertEqual(
                    raised.exception.marker_path_hint,
                    observed_marker,
                )
                self.assertEqual(
                    raised.exception.marker_path_status,
                    "path_unverified",
                )
                self.assertEqual(raised.exception.marker_level, 1)
                self.assertEqual(
                    raised.exception.marker_directory_device,
                    root.stat().st_dev,
                )
                self.assertEqual(
                    raised.exception.marker_directory_inode,
                    root.stat().st_ino,
                )
                self.assertIn(expected_label, str(raised.exception))
                self.assertIn(str(observed_marker), str(raised.exception))

    def test_failed_discovery_resolution_surfaces_marker_stat_errors(
        self,
    ) -> None:
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_stat = project_journal.os.stat
        for error_number in (errno.EACCES, errno.EIO):
            with self.subTest(error_number=error_number):
                root = self.root / f"marker-error-{error_number}"
                cwd = root / "nested"
                cwd.mkdir(parents=True)

                def fail_marker_stat(
                    path: os.PathLike[str] | str,
                    *,
                    dir_fd: int | None = None,
                    follow_symlinks: bool = True,
                ) -> os.stat_result:
                    if (
                        os.fspath(path) == ".git"
                        and dir_fd is not None
                        and not follow_symlinks
                    ):
                        raise OSError(
                            error_number,
                            "injected marker inspection failure",
                            str(path),
                        )
                    return actual_stat(
                        path,
                        dir_fd=dir_fd,
                        follow_symlinks=follow_symlinks,
                    )

                with mock.patch.object(
                    project_journal,
                    "_run_git",
                    return_value=failure,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "stat",
                        side_effect=fail_marker_stat,
                    ):
                        with self.assertRaises(
                            project_journal.RepositoryResolutionError,
                        ) as raised:
                            project_journal._repo_root_for_existing_path(
                                cwd,
                                deadline=time.monotonic() + 5,
                            )

                self.assertEqual(raised.exception.errno, error_number)
                self.assertEqual(
                    raised.exception.resolution_reason,
                    "marker_inspection_failed",
                )
                self.assertIn(
                    ".git inspection failed",
                    str(raised.exception),
                )
                serialized = project_journal._discovery_error(
                    raised.exception,
                )
                self.assertEqual(
                    serialized["code"],
                    "repository_resolution_failed",
                )
                self.assertEqual(
                    serialized["resolution_reason"],
                    "marker_inspection_failed",
                )
                self.assertEqual(serialized["errno"], error_number)
                self.assertEqual(
                    serialized["error_name"],
                    errno.errorcode[error_number],
                )

    def test_failed_discovery_resolution_surfaces_ancestor_stat_errors(
        self,
    ) -> None:
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        for phase, error_number in (
            ("candidate", errno.EACCES),
            ("parent", errno.EIO),
        ):
            with self.subTest(phase=phase):
                cwd = self.root / f"ancestor-error-{phase}"
                cwd.mkdir()
                actual_open = project_journal.os.open
                actual_stat = project_journal.os.stat

                def fail_ancestor_stat(
                    path: os.PathLike[str] | str,
                    *,
                    dir_fd: int | None = None,
                    follow_symlinks: bool = True,
                ) -> os.stat_result:
                    if (
                        phase == "candidate"
                        and pathlib.Path(path) == cwd
                        and follow_symlinks
                    ):
                        raise OSError(
                            error_number,
                            "injected ancestor inspection failure",
                            str(path),
                        )
                    return actual_stat(
                        path,
                        dir_fd=dir_fd,
                        follow_symlinks=follow_symlinks,
                    )

                def fail_parent_open(
                    path: os.PathLike[str] | str,
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    if (
                        phase == "parent"
                        and os.fspath(path) == ".."
                        and dir_fd is not None
                    ):
                        raise OSError(
                            error_number,
                            "injected parent descriptor binding failure",
                            os.fspath(path),
                        )
                    return actual_open(
                        path,
                        flags,
                        mode,
                        dir_fd=dir_fd,
                    )

                with mock.patch.object(
                    project_journal,
                    "_run_git",
                    return_value=failure,
                ) as run:
                    with mock.patch.object(
                        project_journal.os,
                        "stat",
                        side_effect=fail_ancestor_stat,
                    ):
                        with mock.patch.object(
                            project_journal.os,
                            "open",
                            side_effect=fail_parent_open,
                        ):
                            with self.assertRaises(
                                project_journal.RepositoryResolutionError,
                            ) as raised:
                                project_journal._repo_root_for_existing_path(
                                    cwd,
                                    deadline=time.monotonic() + 5,
                                )

                self.assertEqual(raised.exception.errno, error_number)
                self.assertEqual(
                    raised.exception.resolution_reason,
                    "ancestor_inspection_failed",
                )
                if phase == "candidate":
                    run.assert_not_called()
                else:
                    run.assert_called_once()

    def test_failed_discovery_resolution_surfaces_parent_fstat_error(
        self,
    ) -> None:
        cwd = self.root / "parent-fstat-error"
        cwd.mkdir()
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_open = project_journal.os.open
        actual_fstat = project_journal.os.fstat
        actual_close = project_journal.os.close
        parent_fd: int | None = None
        opened_fds: set[int] = set()
        closed_fds: set[int] = set()

        def track_parent_open(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal parent_fd
            result = actual_open(
                path,
                flags,
                mode,
                dir_fd=dir_fd,
            )
            opened_fds.add(result)
            if os.fspath(path) == ".." and dir_fd is not None:
                parent_fd = result
            return result

        def fail_parent_fstat(fd: int) -> os.stat_result:
            if fd == parent_fd:
                raise OSError(
                    errno.EIO,
                    "injected parent fstat failure",
                )
            return actual_fstat(fd)

        def track_close(fd: int) -> None:
            closed_fds.add(fd)
            actual_close(fd)

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "open",
                side_effect=track_parent_open,
            ):
                with mock.patch.object(
                    project_journal.os,
                    "fstat",
                    side_effect=fail_parent_fstat,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "close",
                        side_effect=track_close,
                    ):
                        with self.assertRaises(
                            project_journal.RepositoryResolutionError,
                        ) as raised:
                            project_journal._repo_root_for_existing_path(
                                cwd,
                                deadline=time.monotonic() + 5,
                            )

        self.assertEqual(
            raised.exception.resolution_reason,
            "descriptor_binding_failed",
        )
        self.assertEqual(raised.exception.errno, errno.EIO)
        self.assertEqual(closed_fds, opened_fds)

    def test_failed_discovery_resolution_retries_proved_stale_enotdir(
        self,
    ) -> None:
        root = self.root / "stale-enotdir"
        root.mkdir()
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_stat = project_journal.os.stat
        marker_fds: list[int] = []

        def stale_marker_stat(
            path: os.PathLike[str] | str,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            if os.fspath(path) == ".git" and dir_fd is not None and not follow_symlinks:
                marker_fds.append(dir_fd)
                if len(marker_fds) == 1:
                    raise NotADirectoryError(
                        errno.ENOTDIR,
                        "injected stale ENOTDIR",
                        str(path),
                    )
            return actual_stat(
                path,
                dir_fd=dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "stat",
                side_effect=stale_marker_stat,
            ):
                resolved = project_journal._repo_root_for_existing_path(
                    root,
                    deadline=time.monotonic() + 5,
                )

        self.assertIsNone(resolved)
        self.assertGreaterEqual(len(marker_fds), 2)
        self.assertEqual(marker_fds[0], marker_fds[1])

    def test_failed_discovery_resolution_rejects_persistent_enotdir(
        self,
    ) -> None:
        root = self.root / "persistent-enotdir"
        root.mkdir()
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_stat = project_journal.os.stat

        def persistent_enotdir(
            path: os.PathLike[str] | str,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            if os.fspath(path) == ".git" and dir_fd is not None and not follow_symlinks:
                raise NotADirectoryError(
                    errno.ENOTDIR,
                    "injected persistent ENOTDIR",
                    str(path),
                )
            return actual_stat(
                path,
                dir_fd=dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "stat",
                side_effect=persistent_enotdir,
            ):
                with self.assertRaises(
                    project_journal.RepositoryResolutionError,
                ) as raised:
                    project_journal._repo_root_for_existing_path(
                        root,
                        deadline=time.monotonic() + 5,
                    )

        self.assertEqual(
            raised.exception.resolution_reason,
            "marker_classification_incomplete",
        )
        self.assertEqual(raised.exception.errno, errno.ENOTDIR)

    def test_failed_discovery_resolution_uses_one_deadline_for_marker_scan(
        self,
    ) -> None:
        cwd = self.root / "deadline-candidate"
        cwd.mkdir()
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_open = project_journal.os.open
        actual_close = project_journal.os.close
        actual_stat = project_journal.os.stat
        observed_deadlines: list[float | None] = []
        marker_probe_finished = False
        opened_fds: set[int] = set()
        closed_fds: set[int] = set()

        def expire_during_marker_scan(
            deadline: float | None,
            error: str,
        ) -> None:
            observed_deadlines.append(deadline)
            if marker_probe_finished:
                raise project_journal.UserError(error)

        def finish_marker_probe(
            path: os.PathLike[str] | str,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            nonlocal marker_probe_finished
            try:
                return actual_stat(
                    path,
                    dir_fd=dir_fd,
                    follow_symlinks=follow_symlinks,
                )
            finally:
                if (
                    os.fspath(path) == ".git"
                    and dir_fd is not None
                    and not follow_symlinks
                ):
                    marker_probe_finished = True

        def track_open(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            result = actual_open(
                path,
                flags,
                mode,
                dir_fd=dir_fd,
            )
            opened_fds.add(result)
            return result

        def track_close(fd: int) -> None:
            closed_fds.add(fd)
            actual_close(fd)

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ) as run:
            with mock.patch.object(
                project_journal,
                "_check_deadline",
                side_effect=expire_during_marker_scan,
            ):
                with mock.patch.object(
                    project_journal.os,
                    "stat",
                    side_effect=finish_marker_probe,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "open",
                        side_effect=track_open,
                    ):
                        with mock.patch.object(
                            project_journal.os,
                            "close",
                            side_effect=track_close,
                        ):
                            with self.assertRaises(
                                project_journal.RepositoryResolutionError,
                            ) as raised:
                                project_journal._repo_root_for_existing_path(
                                    cwd,
                                    deadline=123.0,
                                )

        self.assertTrue(marker_probe_finished)
        self.assertTrue(observed_deadlines)
        self.assertEqual(set(observed_deadlines), {123.0})
        self.assertEqual(run.call_args.kwargs["deadline"], 123.0)
        self.assertEqual(closed_fds, opened_fds)
        self.assertEqual(
            raised.exception.resolution_reason,
            "deadline_exceeded",
        )
        self.assertIn("shared deadline", str(raised.exception))

    def test_failed_discovery_resolution_fails_closed_at_marker_scan_limit(
        self,
    ) -> None:
        cwd = self.root / "bounded-marker-scan/a/b/c"
        cwd.mkdir(parents=True)
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_open = project_journal.os.open
        actual_close = project_journal.os.close
        actual_stat = project_journal.os.stat
        active_fds: set[int] = set()
        peak_active_fds = 0
        marker_fds: list[int] = []
        parent_open_count = 0

        def track_open(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal parent_open_count
            nonlocal peak_active_fds
            result = actual_open(
                path,
                flags,
                mode,
                dir_fd=dir_fd,
            )
            active_fds.add(result)
            peak_active_fds = max(peak_active_fds, len(active_fds))
            if os.fspath(path) == ".." and dir_fd is not None:
                parent_open_count += 1
            return result

        def track_close(fd: int) -> None:
            self.assertIn(fd, active_fds)
            active_fds.remove(fd)
            actual_close(fd)

        def track_marker_stat(
            path: os.PathLike[str] | str,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            if os.fspath(path) == ".git" and dir_fd is not None and not follow_symlinks:
                marker_fds.append(dir_fd)
            return actual_stat(
                path,
                dir_fd=dir_fd,
                follow_symlinks=follow_symlinks,
            )

        with mock.patch.object(
            project_journal,
            "MAX_DISCOVERY_CWD_COMPONENTS",
            1,
        ):
            with mock.patch.object(
                project_journal,
                "_run_git",
                return_value=failure,
            ):
                with mock.patch.object(
                    project_journal.os,
                    "open",
                    side_effect=track_open,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "close",
                        side_effect=track_close,
                    ):
                        with mock.patch.object(
                            project_journal.os,
                            "stat",
                            side_effect=track_marker_stat,
                        ):
                            with self.assertRaises(
                                project_journal.RepositoryResolutionError,
                            ) as raised:
                                project_journal._repo_root_for_existing_path(
                                    cwd,
                                    deadline=time.monotonic() + 5,
                                )

        self.assertEqual(
            raised.exception.resolution_reason,
            "marker_scan_limit",
        )
        self.assertIn("2-level bound", str(raised.exception))
        self.assertEqual(peak_active_fds, 2)
        self.assertEqual(active_fds, set())
        self.assertEqual(len(marker_fds), 2)
        self.assertEqual(parent_open_count, 2)

    def test_failed_discovery_resolution_stops_at_device_boundary(self) -> None:
        cwd = self.root / "device-candidate"
        cwd.mkdir()
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )
        actual_open = project_journal.os.open
        actual_fstat = project_journal.os.fstat
        actual_stat = project_journal.os.stat
        parent_fd: int | None = None
        marker_checks: list[tuple[str, int | None]] = []

        def track_parent_open(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal parent_fd
            result = actual_open(
                path,
                flags,
                mode,
                dir_fd=dir_fd,
            )
            if os.fspath(path) == ".." and dir_fd is not None:
                parent_fd = result
            return result

        def cross_device_parent_fstat(fd: int) -> os.stat_result:
            value = actual_fstat(fd)
            if fd == parent_fd:
                return stat_with_dev(value, value.st_dev + 1)
            return value

        def cross_device_parent_stat(
            path: os.PathLike[str] | str,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            if os.fspath(path) == ".git" and not follow_symlinks:
                marker_checks.append((os.fspath(path), dir_fd))
            value = actual_stat(
                path,
                dir_fd=dir_fd,
                follow_symlinks=follow_symlinks,
            )
            return value

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                project_journal.os, "open", side_effect=track_parent_open
            ):
                with mock.patch.object(
                    project_journal.os,
                    "fstat",
                    side_effect=cross_device_parent_fstat,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "stat",
                        side_effect=cross_device_parent_stat,
                    ):
                        resolved = project_journal._repo_root_for_existing_path(
                            cwd,
                            deadline=time.monotonic() + 5,
                        )

        self.assertIsNone(resolved)
        self.assertEqual(len(marker_checks), 1)
        self.assertEqual(marker_checks[0][0], ".git")
        self.assertIsNotNone(marker_checks[0][1])

    def test_failed_discovery_resolution_preserves_kernel_symlink_dotdot_semantics(
        self,
    ) -> None:
        physical = self.root / "physical-marker-root"
        nested = physical / "nested"
        candidate = physical / "candidate"
        nested.mkdir(parents=True)
        candidate.mkdir()
        marker = physical / ".git"
        marker.mkdir()
        link = self.root / "marker-link"
        link.symlink_to(nested, target_is_directory=True)
        kernel_path = link / ".." / "candidate"
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git failure",
        )

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            with mock.patch.object(
                pathlib.Path,
                "resolve",
                side_effect=AssertionError("marker classification must not resolve"),
            ):
                with self.assertRaises(
                    project_journal.RepositoryResolutionError,
                ) as raised:
                    project_journal._repo_root_for_existing_path(
                        kernel_path,
                        deadline=time.monotonic() + 5,
                    )

        self.assertIn(
            "directory descriptor-relative .git entry",
            str(raised.exception),
        )
        self.assertIn(".git", str(raised.exception))

    def test_unsupported_git_does_not_bypass_marker_classification(self) -> None:
        cwd = self.root / "unsupported-git-marker"
        cwd.mkdir()
        marker = cwd / ".git"
        marker.mkdir()

        with mock.patch.object(
            project_journal,
            "_run_git",
            side_effect=project_journal.UnsupportedGitVersion(
                "injected unsupported Git"
            ),
        ):
            with self.assertRaises(
                project_journal.RepositoryResolutionError,
            ) as raised:
                project_journal._repo_root_for_existing_path(
                    cwd,
                    deadline=time.monotonic() + 5,
                )

        self.assertEqual(
            raised.exception.resolution_reason,
            "git_marker_present",
        )
        self.assertEqual(raised.exception.marker_kind, "directory")
        self.assertIsNone(raised.exception.marker_path)
        self.assertEqual(raised.exception.marker_path_hint, marker)
        self.assertEqual(
            raised.exception.marker_path_status,
            "path_unverified",
        )

    def test_frontmatter_and_validation_semantic_caps_fail_closed(self) -> None:
        base_fields = {
            "id": "20260723-a1b2c3",
            "title": "Cap Test",
            "status": "active",
            "created": "2026-07-23",
            "updated": "2026-07-23",
            "branch": "",
            "pr": "",
            "supersedes": [],
            "superseded_by": "",
        }

        too_many_fields = "\n".join(
            ["---"]
            + [
                f"field_{index}: value"
                for index in range(project_journal.MAX_FRONTMATTER_FIELDS + 1)
            ]
            + ["---"]
        )
        with self.assertRaisesRegex(
            project_journal.UserError,
            "frontmatter exceeds",
        ):
            project_journal._parse_frontmatter_text(
                too_many_fields,
                "too-many-fields.md",
            )

        aliases = ", ".join(
            f"alias-{index}"
            for index in range(project_journal.MAX_FRONTMATTER_LIST_ITEMS + 1)
        )
        with self.assertRaisesRegex(project_journal.UserError, "list items"):
            project_journal._parse_frontmatter_text(
                f"---\naliases: [{aliases}]\n---\n",
                "too-many-aliases.md",
            )

        block_supersedes = "\n".join(
            ["---", "supersedes:"]
            + [
                f"  - missing-{index}"
                for index in range(project_journal.MAX_FRONTMATTER_LIST_ITEMS + 1)
            ]
            + ["---"]
        )
        with self.assertRaisesRegex(project_journal.UserError, "list items"):
            project_journal._parse_frontmatter_text(
                block_supersedes,
                "too-many-supersedes.md",
            )

        per_entry = project_journal.JournalEntry(
            path=self.root / "per-entry.md",
            rel_path="docs/project_journal/per-entry.md",
            fields={
                **base_fields,
                "supersedes": [
                    f"missing-{index}"
                    for index in range(
                        project_journal.MAX_VALIDATION_ISSUES_PER_ENTRY + 1
                    )
                ],
            },
        )
        with self.assertRaisesRegex(
            project_journal.UserError,
            "validation issues exceed .* per entry",
        ):
            project_journal._validate_entries([per_entry])

        invalid_entries = [
            project_journal.JournalEntry(
                path=self.root / f"invalid-{index}.md",
                rel_path=f"docs/project_journal/invalid-{index}.md",
                fields={
                    **base_fields,
                    "id": f"20260723-{index:06d}",
                    "status": "invalid",
                    "created": "bad",
                    "updated": "bad",
                },
            )
            for index in range(project_journal.MAX_VALIDATION_ISSUES_TOTAL // 3 + 2)
        ]
        with self.assertRaisesRegex(
            project_journal.UserError,
            "validation issues exceed .* total",
        ):
            project_journal._validate_entries(invalid_entries)

        oversized_issue = project_journal._IssueCollector()
        with self.assertRaisesRegex(
            project_journal.JournalLimitExceeded,
            "validation issue bytes exceed .* total",
        ):
            oversized_issue.add(
                "docs/project_journal/oversized-issue.md",
                "x" * project_journal.MAX_VALIDATION_ISSUES_TOTAL_BYTES,
            )

        with self.assertRaisesRegex(
            project_journal.UserError,
            "entry count exceeds",
        ):
            project_journal._validate_entries(
                [per_entry] * (project_journal.MAX_JOURNAL_ENTRIES + 1)
            )

    def test_issue_collector_bounds_ultralong_index_path_references(self) -> None:
        prefix = b"docs/project_journal/2026/07/"
        suffix = b".md"
        record_overhead = len(b"100644 " + b"a" * 40 + b" 0\t\0")
        path_budget = project_journal.MAX_TRACKED_JOURNAL_INDEX_BYTES - record_overhead
        raw_path = prefix + b"x" * (path_budget - len(prefix) - len(suffix)) + suffix
        rel_path = os.fsdecode(raw_path)
        detail = f"{rel_path}: invalid tracked index frontmatter"
        collector = project_journal._IssueCollector()

        self.assertEqual(
            record_overhead + len(raw_path),
            project_journal.MAX_TRACKED_JOURNAL_INDEX_BYTES,
        )
        for _ in range(project_journal.MAX_VALIDATION_ISSUES_PER_ENTRY):
            collector.add(rel_path, detail)
        report = collector.report()

        self.assertEqual(report.invalid_paths, frozenset({rel_path}))
        self.assertEqual(
            len(report.issues),
            project_journal.MAX_VALIDATION_ISSUES_PER_ENTRY,
        )
        references = {issue.split(": ", 1)[0] for issue in report.issues}
        self.assertEqual(len(references), 1)
        reference = references.pop()
        self.assertTrue(reference.startswith("path_ref="))
        metadata = json.loads(reference.removeprefix("path_ref="))
        self.assertEqual(metadata["bytes"], len(raw_path))
        self.assertEqual(
            metadata["sha256"],
            project_journal.hashlib.sha256(raw_path).hexdigest(),
        )
        self.assertTrue(
            all(
                issue.endswith(": invalid tracked index frontmatter")
                for issue in report.issues
            )
        )
        retained_bytes = sum(
            len(issue.encode("utf-8", errors="backslashreplace")) + 1
            for issue in report.issues
        )
        self.assertLessEqual(
            retained_bytes,
            project_journal.MAX_VALIDATION_ISSUES_TOTAL_BYTES,
        )
        with self.assertRaises(project_journal.JournalLimitExceeded) as raised:
            collector.add(rel_path, detail)
        self.assertNotIn(rel_path, str(raised.exception))
        self.assertIn(reference, str(raised.exception))
        self.assertLess(
            len(str(raised.exception).encode("utf-8")),
            project_journal.MAX_VALIDATION_ISSUE_PATH_BYTES,
        )

    def test_bounded_journal_path_label_counts_rendered_bytes_and_hashes_raw_path(
        self,
    ) -> None:
        short_raw_path = b"docs/project_journal/non-utf8-\xff.md"
        short_rel_path = os.fsdecode(short_raw_path)
        short_label = project_journal._bounded_journal_path_label(short_raw_path)
        expected_short_label = short_rel_path.encode(
            "utf-8",
            errors="backslashreplace",
        ).decode("utf-8")

        self.assertEqual(short_label, expected_short_label)
        self.assertEqual(short_label.encode("utf-8").decode("utf-8"), short_label)
        self.assertEqual(os.fsencode(short_rel_path), short_raw_path)
        self.assertLessEqual(
            len(short_label.encode("utf-8")),
            project_journal.MAX_VALIDATION_ISSUE_PATH_BYTES,
        )
        collector = project_journal._IssueCollector()
        collector.add(short_rel_path, "injected short-path error")
        report = collector.report()
        self.assertEqual(report.invalid_paths, frozenset({short_rel_path}))
        self.assertEqual(
            report.issues,
            (f"{short_label}: injected short-path error",),
        )

        prefix = b"docs/project_journal/"
        rendered_expansion = prefix + b"\xff" * 700 + b".md"
        self.assertLess(
            len(rendered_expansion),
            project_journal.MAX_VALIDATION_ISSUE_PATH_BYTES,
        )
        self.assertGreater(
            len(
                os.fsdecode(rendered_expansion).encode(
                    "utf-8",
                    errors="backslashreplace",
                )
            ),
            project_journal.MAX_VALIDATION_ISSUE_PATH_BYTES,
        )

        reference = project_journal._bounded_journal_path_label(rendered_expansion)
        metadata = json.loads(reference.removeprefix("path_ref="))

        self.assertTrue(reference.startswith("path_ref="))
        self.assertEqual(metadata["bytes"], len(rendered_expansion))
        self.assertEqual(
            metadata["sha256"],
            project_journal.hashlib.sha256(rendered_expansion).hexdigest(),
        )
        self.assertEqual(
            project_journal._bounded_journal_path_label(
                os.fsdecode(rendered_expansion)
            ),
            reference,
        )

    def test_discovery_json_output_writes_short_non_utf8_label_to_strict_sink(
        self,
    ) -> None:
        raw_path = b"docs/project_journal/non-utf8-\xff.md"
        label = project_journal._bounded_journal_path_label(raw_path)
        rows = [
            {
                "repo": "/repo",
                "adoption_error": {
                    "code": "journal_semantic_limit_exceeded",
                    "message": f"{label}: injected semantic limit",
                },
            }
        ]
        raw_stdout = io.BytesIO()
        strict_stdout = io.TextIOWrapper(
            raw_stdout,
            encoding="utf-8",
            errors="strict",
        )
        args = mock.Mock(
            codex_home=str(self.root),
            since_days=1,
            json_output=True,
        )

        with mock.patch.object(
            project_journal,
            "_discover_repos",
            return_value=rows,
        ):
            with mock.patch.object(project_journal.sys, "stdout", strict_stdout):
                project_journal.command_discover_repos(args)
                strict_stdout.flush()

        rendered = raw_stdout.getvalue()
        decoded = rendered.decode("utf-8", errors="strict")
        self.assertIn("\\udcff", decoded)
        self.assertEqual(json.loads(decoded), rows)

    def test_discovery_json_output_writes_non_utf8_hooks_path_error_to_strict_sink(
        self,
    ) -> None:
        repo = self.init_repo().resolve()
        raw_path = b".githooks-\xff"
        configured = project_journal._parse_repo_hook_path_config(
            b"local\0file:/repo/.git/config\0" + raw_path + b"\0",
            "test effective core.hooksPath query",
        )

        with mock.patch.object(
            project_journal,
            "_repo_hook_path_config",
            return_value=configured,
        ):
            with mock.patch.object(
                project_journal,
                "_allowed_hook_roots",
                return_value=[repo],
            ):
                with mock.patch.object(
                    project_journal.os,
                    "open",
                    side_effect=OSError(
                        errno.EACCES,
                        "injected hook access failure",
                        os.fspath(repo / configured.raw_path / "post-merge"),
                    ),
                ):
                    with self.assertRaises(
                        project_journal.DiscoveryAuxiliaryInspectionError,
                    ) as raised:
                        project_journal._has_hook_marker(repo)

        undecodable_byte = os.fsdecode(b"\xff")
        self.assertIn(undecodable_byte, str(raised.exception))
        discovery_error = project_journal._discovery_error(raised.exception)
        self.assertNotIn(undecodable_byte, discovery_error["message"])
        self.assertIn("\\udcff", discovery_error["message"])
        rows = [
            {
                "repo": str(repo),
                "discovery_error": {"hooks_installed": discovery_error},
            }
        ]
        raw_stdout = io.BytesIO()
        strict_stdout = io.TextIOWrapper(
            raw_stdout,
            encoding="utf-8",
            errors="strict",
        )
        args = mock.Mock(
            codex_home=str(self.root),
            since_days=1,
            json_output=True,
        )

        with mock.patch.object(
            project_journal,
            "_discover_repos",
            return_value=rows,
        ):
            with mock.patch.object(project_journal.sys, "stdout", strict_stdout):
                project_journal.command_discover_repos(args)
                strict_stdout.flush()

        decoded = raw_stdout.getvalue().decode("utf-8", errors="strict")
        self.assertIn("\\udcff", decoded)
        self.assertEqual(json.loads(decoded), rows)

    def test_discovery_error_sanitizes_nested_non_utf8_display_fields(
        self,
    ) -> None:
        undecodable_byte = os.fsdecode(b"\xff")
        error = project_journal.RepositoryResolutionError(
            pathlib.Path("/repo"),
            "injected resolution failure",
            resolution_reason="marker_unreadable",
            marker_path=pathlib.Path(f"/marker-{undecodable_byte}"),
            marker_path_hint=pathlib.Path(f"/hint-{undecodable_byte}"),
        )
        error.cleanup_errors.append(
            {
                "context": f"cleanup-{undecodable_byte}",
                "error_type": "OSError",
                "message": f"failure-{undecodable_byte}",
                "details": [
                    f"detail-{undecodable_byte}",
                    {"nested": f"value-{undecodable_byte}"},
                ],
            }
        )

        rendered = project_journal._discovery_error(error)
        encoded = json.dumps(rendered, ensure_ascii=False).encode(
            "utf-8",
            errors="strict",
        )
        decoded = encoded.decode("utf-8", errors="strict")

        self.assertEqual(rendered["code"], "repository_resolution_failed")
        self.assertEqual(rendered["resolution_reason"], "marker_unreadable")
        self.assertNotIn(undecodable_byte, decoded)
        self.assertGreaterEqual(decoded.count("\\\\udcff"), 5)

    def test_discovery_json_output_writes_non_utf8_rollout_error_to_strict_sink(
        self,
    ) -> None:
        codex_home = self.root / "codex-home"
        source = pathlib.Path(os.fsdecode(b"/sessions/rollout-invalid-name-\xff.jsonl"))
        rollout = project_journal._RolloutCandidate(
            path=source,
            object_identity=(1, 2),
            access_policy=(os.getuid(), os.getgid(), 0o600),
            size=16,
            mtime=time.time(),
            observed_date=project_journal.dt.date(2026, 5, 5),
        )

        raw_stdout = io.BytesIO()
        strict_stdout = io.TextIOWrapper(
            raw_stdout,
            encoding="utf-8",
            errors="strict",
        )
        args = mock.Mock(
            codex_home=str(codex_home),
            since_days=9999,
            json_output=True,
        )

        with (
            mock.patch.object(
                project_journal,
                "_iter_rollout_paths",
                side_effect=([rollout], []),
            ),
            mock.patch.object(
                project_journal,
                "_extract_cwds",
                side_effect=project_journal.DiscoveryRolloutParseError(
                    parse_reason="invalid_json",
                    record_number=1,
                    byte_offset=0,
                    detail="injected parse failure",
                ),
            ),
            mock.patch.object(project_journal.sys, "stdout", strict_stdout),
        ):
            project_journal.command_discover_repos(args)
            strict_stdout.flush()

        rows = json.loads(raw_stdout.getvalue().decode("utf-8", errors="strict"))
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["code"], "discovery_rollout_parse_failed")
        self.assertEqual(coverage["parse_reason"], "invalid_json")
        self.assertIn("\\udcff", coverage["source"])
        self.assertNotIn(os.fsdecode(b"\xff"), coverage["source"])

    def test_frontmatter_line_cap_does_not_count_long_body(self) -> None:
        body = "\n".join(
            f"Body line {index}"
            for index in range(project_journal.MAX_FRONTMATTER_LINES + 100)
        )
        fields = project_journal._parse_frontmatter_text(
            "---\n"
            "id: 20260723-longbody\n"
            "title: Long body\n"
            "status: active\n"
            "created: 2026-07-23\n"
            "updated: 2026-07-23\n"
            "branch:\n"
            "pr:\n"
            "supersedes: []\n"
            "superseded_by:\n"
            "---\n\n"
            f"{body}\n",
            "long-body.md",
        )

        self.assertEqual(fields["id"], "20260723-longbody")

    def test_frontmatter_line_cap_still_bounds_opening_block(self) -> None:
        oversized_frontmatter = "\n".join(
            ["---", "title: oversized"]
            + [
                "  ignored continuation"
                for _ in range(project_journal.MAX_FRONTMATTER_LINES)
            ]
            + ["---"]
        )

        with self.assertRaisesRegex(
            project_journal.JournalLimitExceeded,
            "frontmatter exceeds",
        ):
            project_journal._parse_frontmatter_text(
                oversized_frontmatter,
                "oversized-frontmatter.md",
            )

    def test_semantic_cap_in_one_repo_isolated_by_discovery(self) -> None:
        healthy = self.init_repo("healthy-semantic")
        healthy_journal = self.write_journal(
            healthy,
            "docs/project_journal/2026/07/healthy.md",
            entry_id="20260723-healthy",
            title="Healthy",
            status="active",
            updated="2026-07-23",
        )
        self.assertEqual(
            run_git(
                healthy,
                "add",
                "--",
                str(healthy_journal.relative_to(healthy)),
            ).returncode,
            0,
        )

        oversized = self.init_repo("oversized-semantic")
        oversized_journal = self.write_journal(
            oversized,
            "docs/project_journal/2026/07/oversized.md",
            entry_id="20260723-oversized",
            title="Oversized",
            status="active",
            updated="2026-07-23",
        )
        aliases = ", ".join(
            f"alias-{index}"
            for index in range(project_journal.MAX_FRONTMATTER_LIST_ITEMS + 1)
        )
        oversized_journal.write_text(
            oversized_journal.read_text(encoding="utf-8").replace(
                "---\n\n## Summary",
                f"aliases: [{aliases}]\n---\n\n## Summary",
            ),
            encoding="utf-8",
        )
        self.assertEqual(
            run_git(
                oversized,
                "add",
                "--",
                str(oversized_journal.relative_to(oversized)),
            ).returncode,
            0,
        )

        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/07/23"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-semantic-caps.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(healthy)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(oversized)}})
            + "\n",
            encoding="utf-8",
        )
        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {
            pathlib.Path(row["repo"]).name: row for row in json.loads(result.stdout)
        }
        self.assertEqual(rows["healthy-semantic"]["adoption_status"], "adopted")
        oversized_row = rows["oversized-semantic"]
        self.assertEqual(oversized_row["adoption_status"], "inconclusive")
        self.assertEqual(
            oversized_row["adoption_error"]["code"],
            "journal_semantic_limit_exceeded",
        )
        self.assertIn("list items", oversized_row["adoption_error"]["message"])

    def test_parse_index_journal_blobs_handles_raw_paths_and_rejects_malformed(
        self,
    ) -> None:
        oid = b"a" * 40
        raw_path = b"docs/project_journal/2026/05/non-utf8-\xff.md"
        parsed = project_journal._parse_index_journal_blobs(
            b"100644 " + oid + b" 0\t" + raw_path + b"\0"
        )
        self.assertEqual(len(parsed), 1)
        self.assertEqual(os.fsencode(parsed[0].rel_path), raw_path)

        exact_root_records = b"".join(
            mode + b" " + oid + b" 0\tdocs/project_journal\0"
            for mode in (b"100644", b"120000", b"160000")
        )
        parsed_with_root_entries = project_journal._parse_index_journal_blobs(
            exact_root_records + b"100644 " + oid + b" 0\t" + raw_path + b"\0"
        )
        self.assertEqual(len(parsed_with_root_entries), 1)
        self.assertEqual(os.fsencode(parsed_with_root_entries[0].rel_path), raw_path)

        malformed_outputs = (
            b"100644 " + oid + b" 0\t" + raw_path,
            b"100644 not-an-oid 0\t" + raw_path + b"\0",
            b"100644 not-an-oid 0\tdocs/project_journal\0",
            b"100644 " + oid + b" 0 docs/project_journal/bad.md\0",
            b"100644 " + oid + b" 0\tdocs/project_journal/../outside.md\0",
            b"100644 " + oid + b" 0\tdocs/project_journal-other/bad.md\0",
        )
        for output in malformed_outputs:
            with self.subTest(output=output):
                with self.assertRaises(project_journal.UserError):
                    project_journal._parse_index_journal_blobs(output)

    def test_index_stream_parser_enforces_record_limit_while_feeding(
        self,
    ) -> None:
        oid = b"a" * 40
        records = b"".join(
            b"100644 "
            + oid
            + b" 0\tdocs/project_journal/2026/05/entry-"
            + str(index).encode("ascii")
            + b".md\0"
            for index in range(3)
        )
        parser = project_journal._IndexStageStreamParser(max_records=2)

        with self.assertRaisesRegex(
            project_journal.UserError,
            "exceeds 2 records",
        ):
            parser.feed(records)

    def test_cat_file_batch_parser_accepts_fragmented_binary_records(
        self,
    ) -> None:
        oids = ("a" * 40, "b" * 64)
        blobs = [
            project_journal.IndexJournalBlob(
                mode=b"100644",
                oid=oid,
                raw_path=f"docs/project_journal/{index}.md".encode(),
                rel_path=f"docs/project_journal/{index}.md",
            )
            for index, oid in enumerate(oids)
        ]
        expected = [b"alpha\n\x00omega", b""]
        response = b"".join(
            (f"{blob.oid} blob {len(content)}\n".encode("ascii") + content + b"\n")
            for blob, content in zip(blobs, expected)
        )
        parser = project_journal._CatFileBatchStreamParser(blobs)

        for offset in range(0, len(response), 7):
            parser.feed(response[offset : offset + 7])
        parser.finish()

        self.assertEqual(parser.contents(), expected)

    def test_cat_file_batch_parser_rejects_malformed_or_mismatched_records(
        self,
    ) -> None:
        oid = "a" * 40
        blob = project_journal.IndexJournalBlob(
            mode=b"100644",
            oid=oid,
            raw_path=b"docs/project_journal/entry.md",
            rel_path="docs/project_journal/entry.md",
        )
        cases = (
            ("malformed git cat-file batch header", b"garbage\n"),
            (
                "mismatched object id",
                f"{'b' * 40} blob 0\n\n".encode("ascii"),
            ),
            ("expected 'blob'", f"{oid} tree 0\n\n".encode("ascii")),
            ("invalid git cat-file batch object size", f"{oid} blob x\n".encode()),
            (
                "malformed git cat-file batch content framing",
                f"{oid} blob 1\nx!".encode(),
            ),
            ("index blob is missing", f"{oid} missing\n".encode()),
            ("truncated git cat-file batch content", f"{oid} blob 2\nx".encode()),
            ("header exceeds 128 bytes", b"x" * 129),
            ("fewer records than requested", b""),
            (
                "unexpected extra response data",
                f"{oid} blob 0\n\nx".encode(),
            ),
        )

        for expected_error, response in cases:
            with self.subTest(expected_error=expected_error):
                parser = project_journal._CatFileBatchStreamParser([blob])
                with self.assertRaisesRegex(
                    project_journal.UserError,
                    expected_error,
                ):
                    parser.feed(response)
                    parser.finish()

        invalid_blob = project_journal.IndexJournalBlob(
            mode=b"100644",
            oid="not-an-oid",
            raw_path=blob.raw_path,
            rel_path=blob.rel_path,
        )
        with self.assertRaisesRegex(
            project_journal.UserError,
            "invalid requested index blob object id",
        ):
            project_journal._CatFileBatchStreamParser([invalid_blob])
        with self.assertRaisesRegex(
            project_journal.UserError,
            "blob batch exceeds 0 records",
        ):
            project_journal._CatFileBatchStreamParser([blob], max_records=0)

    def test_cat_file_batch_parser_bounds_every_path_bearing_error(self) -> None:
        oid = "a" * 40
        raw_path = b"docs/project_journal/" + b"\xff" * 700 + b"-cat-file-error.md"
        label = project_journal._bounded_journal_path_label(raw_path)
        blob = project_journal.IndexJournalBlob(
            mode=b"100644",
            oid=oid,
            raw_path=raw_path,
            rel_path=os.fsdecode(raw_path),
        )
        cases = (
            b"garbage\n",
            f"{'b' * 40} blob 0\n\n".encode("ascii"),
            f"{oid} missing\n".encode("ascii"),
            f"{oid} tree 0\n\n".encode("ascii"),
            f"{oid} blob x\n".encode("ascii"),
            f"{oid} blob 2\nx".encode("ascii"),
            f"{oid} blob 1\nx!".encode("ascii"),
            b"",
            b"partial-header",
            b"x" * (project_journal.MAX_CAT_FILE_BATCH_HEADER_BYTES + 1),
        )

        for response in cases:
            with self.subTest(response=response[:32]):
                parser = project_journal._CatFileBatchStreamParser([blob])
                with self.assertRaises(project_journal.UserError) as raised:
                    parser.feed(response)
                    parser.finish()
                message = str(raised.exception)
                self.assertIn(label, message)
                self.assertNotIn(blob.rel_path, message)
                self.assertLessEqual(
                    len(message.encode("utf-8", errors="backslashreplace")),
                    project_journal.MAX_VALIDATION_ISSUE_PATH_BYTES,
                )

        invalid_blob = project_journal.dataclasses.replace(
            blob,
            oid="not-an-oid",
        )
        non_ascii_blob = project_journal.dataclasses.replace(
            blob,
            oid="\udcff",
        )
        for invalid in (invalid_blob, non_ascii_blob):
            with self.subTest(invalid_oid=invalid.oid):
                with self.assertRaises(project_journal.UserError) as raised:
                    project_journal._CatFileBatchStreamParser([invalid])
                self.assertIn(label, str(raised.exception))
                self.assertNotIn(blob.rel_path, str(raised.exception))

    def test_index_frontmatter_and_semantic_limits_share_bounded_path_label(
        self,
    ) -> None:
        raw_path = b"docs/project_journal/" + b"\xff" * 700 + b"-frontmatter-limit.md"
        rel_path = os.fsdecode(raw_path)
        label = project_journal._bounded_journal_path_label(raw_path)
        oversized_frontmatter = "\n".join(
            ["---", "title: oversized"]
            + [
                "  ignored continuation"
                for _ in range(project_journal.MAX_FRONTMATTER_LINES)
            ]
            + ["---"]
        ).encode("utf-8")
        blob = project_journal.IndexJournalBlob(
            mode=b"100644",
            oid="a" * 40,
            raw_path=raw_path,
            rel_path=rel_path,
        )

        with mock.patch.object(
            project_journal,
            "_tracked_index_journal_snapshot",
            side_effect=((b"stable", [blob]), (b"stable", [blob])),
        ):
            with mock.patch.object(
                project_journal,
                "_read_index_blobs_batch",
                return_value=[oversized_frontmatter],
            ):
                with self.assertRaises(project_journal.JournalLimitExceeded) as raised:
                    project_journal._load_entries_from_index_report(self.root)

        self.assertIn(label, str(raised.exception))
        self.assertNotIn(rel_path, str(raised.exception))

        entry = project_journal.JournalEntry(
            path=self.root / "bounded-semantic.md",
            rel_path=rel_path,
            fields={
                "id": "20260730-bounded-path",
                "title": "Bounded semantic path",
                "status": "active",
                "created": "2026-07-30",
                "updated": "2026-07-30",
                "supersedes": [
                    f"missing-{index}"
                    for index in range(project_journal.MAX_FRONTMATTER_LIST_ITEMS + 1)
                ],
            },
        )
        with self.assertRaises(project_journal.JournalLimitExceeded) as semantic_raised:
            project_journal._validate_entries([entry])

        self.assertIn(label, str(semantic_raised.exception))
        self.assertNotIn(rel_path, str(semantic_raised.exception))

    def test_indexed_ordinary_error_uses_one_bounded_label_and_exact_invalid_path(
        self,
    ) -> None:
        raw_path = b"docs/project_journal/" + b"\xff" * 700 + b"-ordinary-error.md"
        rel_path = os.fsdecode(raw_path)
        label = project_journal._bounded_journal_path_label(raw_path)
        blob = project_journal.IndexJournalBlob(
            mode=b"100644",
            oid="a" * 40,
            raw_path=raw_path,
            rel_path=rel_path,
        )

        with mock.patch.object(
            project_journal,
            "_tracked_index_journal_snapshot",
            side_effect=((b"stable", [blob]), (b"stable", [blob])),
        ):
            with mock.patch.object(
                project_journal,
                "_read_index_blobs_batch",
                return_value=[b"missing frontmatter\n"],
            ):
                loaded = project_journal._load_entries_from_index_report(self.root)

        self.assertEqual(
            loaded.validation.invalid_paths,
            frozenset({rel_path}),
        )
        self.assertEqual(len(loaded.validation.issues), 1)
        issue = loaded.validation.issues[0]
        self.assertEqual(issue.count(label), 1)
        self.assertNotIn(rel_path, issue)
        discovery_error = project_journal._discovery_error(
            project_journal.JournalLimitExceeded(f"{label}: injected semantic limit")
        )
        self.assertEqual(
            discovery_error["code"],
            "journal_semantic_limit_exceeded",
        )
        self.assertIn(label, discovery_error["message"])
        self.assertNotIn(rel_path, discovery_error["message"])

    def test_cat_file_batch_parser_enforces_blob_and_total_byte_limits(
        self,
    ) -> None:
        oids = ("a" * 40, "b" * 40)
        blobs = [
            project_journal.IndexJournalBlob(
                mode=b"100644",
                oid=oid,
                raw_path=f"docs/project_journal/{index}.md".encode(),
                rel_path=f"docs/project_journal/{index}.md",
            )
            for index, oid in enumerate(oids)
        ]
        oversized = project_journal._CatFileBatchStreamParser(
            blobs[:1],
            max_blob_bytes=3,
            max_total_bytes=5,
        )
        with self.assertRaisesRegex(
            project_journal.UserError,
            "index blob exceeds 3 bytes",
        ):
            oversized.feed(f"{oids[0]} blob 4\n".encode("ascii"))

        aggregate = project_journal._CatFileBatchStreamParser(
            blobs,
            max_blob_bytes=3,
            max_total_bytes=5,
        )
        with self.assertRaisesRegex(
            project_journal.UserError,
            "tracked journal blobs exceed 5 total bytes",
        ):
            aggregate.feed(f"{oids[0]} blob 3\nabc\n{oids[1]} blob 3\n".encode("ascii"))

    def test_cat_file_batch_reads_max_records_with_one_process(self) -> None:
        repo = self.init_repo()
        source = self.root / "batch-source"
        source.write_bytes(b"x")
        hashed = run_git(repo, "hash-object", "-w", str(source))
        self.assertEqual(hashed.returncode, 0, hashed.stderr)
        oid = hashed.stdout.strip()
        blobs = [
            project_journal.IndexJournalBlob(
                mode=b"100644",
                oid=oid,
                raw_path=f"docs/project_journal/{index}.md".encode(),
                rel_path=f"docs/project_journal/{index}.md",
            )
            for index in range(project_journal.MAX_TRACKED_JOURNAL_RECORDS)
        ]
        original_popen = project_journal.subprocess.Popen
        poison = {
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(self.root / "alternates"),
            "GIT_COMMON_DIR": str(self.root / "common"),
            "GIT_CONFIG": str(self.root / "config"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_VALUE_0": str(self.root / "other-worktree"),
            "GIT_DIR": str(self.root / "other-git-dir"),
            "GIT_EXEC_PATH": str(self.root / "git-exec"),
            "GIT_INDEX_FILE": str(self.root / "other-index"),
            "GIT_NO_LAZY_FETCH": "0",
            "GIT_OBJECT_DIRECTORY": str(self.root / "objects"),
            "GIT_WORK_TREE": str(self.root / "other-worktree"),
        }

        with mock.patch.dict(os.environ, poison, clear=False):
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=original_popen,
            ) as popen:
                contents = project_journal._read_index_blobs_batch(repo, blobs)

        self.assertEqual(popen.call_count, 1)
        self.assertEqual(len(contents), project_journal.MAX_TRACKED_JOURNAL_RECORDS)
        self.assertEqual(set(contents), {b"x"})
        self.assertEqual(popen.call_args.args[0][-2:], ["cat-file", "--batch"])
        child_env = popen.call_args.kwargs["env"]
        expected_git_env = {
            key: value
            for key, value in project_journal.SAFE_GIT_ENV.items()
            if key.startswith("GIT_")
        }
        actual_git_env = {
            key: value for key, value in child_env.items() if key.startswith("GIT_")
        }
        self.assertEqual(actual_git_env, expected_git_env)
        self.assertNotIn("GIT_DIR", child_env)
        self.assertNotIn("GIT_INDEX_FILE", child_env)
        self.assertNotIn("GIT_OBJECT_DIRECTORY", child_env)
        self.assertEqual(child_env["GIT_NO_LAZY_FETCH"], "1")
        self.assertEqual(child_env["GIT_NO_REPLACE_OBJECTS"], "1")
        self.assertEqual(child_env["GIT_TERMINAL_PROMPT"], "0")
        self.assertEqual(child_env["GIT_ASKPASS"], "")
        self.assertIn("--no-optional-locks", popen.call_args.args[0])
        self.assertIn("credential.helper=", popen.call_args.args[0])
        self.assertIn("credential.interactive=never", popen.call_args.args[0])

    def test_bounded_capture_does_not_spawn_without_selector(self) -> None:
        with mock.patch.object(
            project_journal.selectors,
            "DefaultSelector",
            side_effect=OSError("too many open files"),
        ):
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
            ) as popen:
                with self.assertRaisesRegex(
                    project_journal.UserError,
                    "failed to create Git index snapshot selector",
                ):
                    self.capture_process(
                        [sys.executable, "-c", "pass"],
                        timeout_seconds=1,
                        stdout_limit=1024,
                    )

        popen.assert_not_called()

    def test_unsupported_runtime_is_rejected_before_git_selection(self) -> None:
        original_runtime = project_journal._GIT_RUNTIME
        original_error = project_journal._GIT_RUNTIME_ERROR
        try:
            for os_name, platform in (
                ("nt", "win32"),
                ("posix", "freebsd14"),
            ):
                with self.subTest(os_name=os_name, platform=platform):
                    project_journal._GIT_RUNTIME = None
                    project_journal._GIT_RUNTIME_ERROR = None
                    with mock.patch.object(project_journal.os, "name", os_name):
                        with mock.patch.object(
                            project_journal.sys,
                            "platform",
                            platform,
                        ):
                            with mock.patch.object(
                                project_journal.shutil,
                                "which",
                            ) as which:
                                project_journal._initialize_git_runtime()
                    self.assertIsInstance(
                        project_journal._GIT_RUNTIME_ERROR,
                        project_journal.UnsupportedPlatform,
                    )
                    self.assertIn(
                        "requires macOS or Linux",
                        str(project_journal._GIT_RUNTIME_ERROR),
                    )
                    which.assert_not_called()
        finally:
            project_journal._GIT_RUNTIME = original_runtime
            project_journal._GIT_RUNTIME_ERROR = original_error

    def test_other_posix_cli_fails_before_git_selection_or_execution(self) -> None:
        original_runtime = project_journal._GIT_RUNTIME
        original_error = project_journal._GIT_RUNTIME_ERROR
        stdout = io.StringIO()
        stderr = io.StringIO()
        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(project_journal.os, "name", "posix"):
                with mock.patch.object(project_journal.sys, "platform", "freebsd14"):
                    with mock.patch.object(
                        project_journal.shutil,
                        "which",
                    ) as which:
                        with mock.patch.object(project_journal, "_run_git") as run_git:
                            with mock.patch.object(
                                project_journal.sys,
                                "stdout",
                                stdout,
                            ):
                                with mock.patch.object(
                                    project_journal.sys,
                                    "stderr",
                                    stderr,
                                ):
                                    status = project_journal.main(
                                        [
                                            "discover-repos",
                                            "--codex-home",
                                            str(self.root / "missing-codex-home"),
                                            "--json",
                                        ]
                                    )
        finally:
            project_journal._GIT_RUNTIME = original_runtime
            project_journal._GIT_RUNTIME_ERROR = original_error

        self.assertEqual(status, 1)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("requires macOS or Linux", stderr.getvalue())
        which.assert_not_called()
        run_git.assert_not_called()

    @unittest.skipUnless(
        os.name == "posix"
        and project_journal._posix_waitid_status_observation_available(),
        "POSIX waitid/WNOWAIT process-status contract",
    )
    def test_process_status_observation_preserves_leader_fence(self) -> None:
        process = mock.Mock()
        process.pid = 12345
        status = mock.Mock(
            si_pid=process.pid,
            si_code=os.CLD_EXITED,
            si_status=7,
        )

        with mock.patch.object(
            project_journal.os, "waitid", return_value=status
        ) as waitid:
            returncode = project_journal._wait_for_process_status_without_reaping(
                process,
                time.monotonic() + 1,
                "timed out",
            )

        self.assertEqual(returncode, 7)
        waitid.assert_called_once()
        _id_type, observed_pid, options = waitid.call_args.args
        self.assertEqual(observed_pid, process.pid)
        self.assertTrue(options & os.WNOWAIT)
        process.poll.assert_not_called()
        process.wait.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX SIGCHLD waitability contract")
    def test_process_launch_rejects_ignored_sigchld_before_popen_or_killpg(
        self,
    ) -> None:
        with mock.patch.object(
            project_journal.signal,
            "getsignal",
            return_value=signal.SIG_IGN,
        ):
            with mock.patch.object(project_journal.subprocess, "Popen") as popen:
                with mock.patch.object(project_journal.os, "killpg") as killpg:
                    with self.assertRaises(
                        project_journal._WaitableSigchldUnavailable
                    ) as raised:
                        self.capture_process(
                            [sys.executable, "-c", "raise SystemExit(7)"],
                            timeout_seconds=5,
                            stdout_limit=1024,
                        )

        self.assertEqual(
            raised.exception.code,
            "waitable_sigchld_unavailable",
        )
        self.assertIn("SIGCHLD is ignored", str(raised.exception))
        popen.assert_not_called()
        killpg.assert_not_called()

    @unittest.skipUnless(sys.platform == "darwin", "Darwin SA_NOCLDWAIT contract")
    def test_process_launch_rejects_darwin_no_cldwait_before_popen(
        self,
    ) -> None:
        with mock.patch.object(
            project_journal,
            "_darwin_sigchld_action",
            return_value=(0, project_journal.DARWIN_SA_NOCLDWAIT),
        ):
            with mock.patch.object(project_journal.subprocess, "Popen") as popen:
                with self.assertRaises(
                    project_journal._WaitableSigchldUnavailable
                ) as raised:
                    self.capture_process(
                        [sys.executable, "-c", "raise SystemExit(7)"],
                        timeout_seconds=5,
                        stdout_limit=1024,
                    )

        self.assertIn("SA_NOCLDWAIT", str(raised.exception))
        popen.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "Linux SA_NOCLDWAIT contract")
    @mock.patch.object(
        project_journal,
        "_require_process_status_observation_support",
    )
    def test_process_launch_rejects_linux_no_cldwait_before_popen_or_killpg(
        self,
        status_support: mock.Mock,
    ) -> None:
        with mock.patch.object(project_journal.sys, "platform", "linux"):
            with mock.patch.object(
                project_journal.signal,
                "getsignal",
                return_value=signal.SIG_DFL,
            ):
                with mock.patch.object(
                    project_journal,
                    "_linux_sigchld_action",
                    return_value=(0, project_journal.LINUX_SA_NOCLDWAIT),
                ):
                    with mock.patch.object(
                        project_journal.subprocess,
                        "Popen",
                    ) as popen:
                        with mock.patch.object(project_journal.os, "killpg") as killpg:
                            with self.assertRaises(
                                project_journal._WaitableSigchldUnavailable
                            ) as raised:
                                self.capture_process(
                                    [
                                        sys.executable,
                                        "-c",
                                        "raise SystemExit(7)",
                                    ],
                                    timeout_seconds=5,
                                    stdout_limit=1024,
                                )

        self.assertIn("Linux SIGCHLD has SA_NOCLDWAIT", str(raised.exception))
        status_support.assert_called_once_with()
        popen.assert_not_called()
        killpg.assert_not_called()

    def test_linux_sigaction_layout_matches_reviewed_lp64_libc_abi(self) -> None:
        if ctypes.sizeof(ctypes.c_void_p) != 8 or ctypes.sizeof(ctypes.c_ulong) != 8:
            self.skipTest("reviewed Linux sigaction layouts require an LP64 ABI")
        self.assertEqual(ctypes.sizeof(project_journal._LinuxSigaction), 152)
        self.assertEqual(project_journal._LinuxSigaction.handler.offset, 0)
        self.assertEqual(project_journal._LinuxSigaction.mask.offset, 8)
        self.assertEqual(project_journal._LinuxSigaction.flags.offset, 136)
        self.assertEqual(project_journal._LinuxSigaction.restorer.offset, 144)

    @unittest.skipUnless(os.name == "posix", "Linux sigaction ABI contract")
    @mock.patch.object(
        project_journal,
        "_require_process_status_observation_support",
    )
    def test_unreviewed_and_musl_linux_multiarch_fail_before_native_operations(
        self,
        status_support: mock.Mock,
    ) -> None:
        rejected_abis = (
            ("x86_64", "x86_64-linux-unknown"),
            ("x86_64", "x86_64-linux-musl"),
            ("aarch64", "aarch64-linux-musl"),
        )
        for machine, multiarch in rejected_abis:
            with self.subTest(machine=machine, multiarch=multiarch):
                uname = mock.Mock(machine=machine)
                implementation = mock.Mock(_multiarch=multiarch)
                with mock.patch.object(project_journal.sys, "platform", "linux"):
                    with mock.patch.object(
                        project_journal.sys,
                        "implementation",
                        implementation,
                    ):
                        with mock.patch.object(
                            project_journal.signal,
                            "getsignal",
                            return_value=signal.SIG_DFL,
                        ):
                            with mock.patch.object(
                                project_journal.os,
                                "uname",
                                return_value=uname,
                            ):
                                with mock.patch.object(
                                    project_journal.ctypes,
                                    "CDLL",
                                ) as cdll:
                                    with mock.patch.object(
                                        project_journal.subprocess,
                                        "Popen",
                                    ) as popen:
                                        with mock.patch.object(
                                            project_journal.os,
                                            "killpg",
                                        ) as killpg:
                                            with self.assertRaises(
                                                project_journal._WaitableSigchldUnavailable
                                            ) as raised:
                                                self.capture_process(
                                                    [
                                                        sys.executable,
                                                        "-c",
                                                        "raise SystemExit(7)",
                                                    ],
                                                    timeout_seconds=5,
                                                    stdout_limit=1024,
                                                )

                self.assertIn(
                    "no reviewed sigaction layout",
                    str(raised.exception),
                )
                cdll.assert_not_called()
                popen.assert_not_called()
                killpg.assert_not_called()

        self.assertEqual(
            status_support.call_args_list,
            [mock.call()] * len(rejected_abis),
        )

    @unittest.skipUnless(os.name == "posix", "Linux SA_NOCLDWAIT contract")
    def test_linux_no_cldwait_after_spawn_loses_numeric_group_identity(
        self,
    ) -> None:
        process = mock.Mock(spec=["pid"])
        process.pid = 12345
        with mock.patch.object(project_journal.sys, "platform", "linux"):
            with mock.patch.object(
                project_journal.signal,
                "getsignal",
                return_value=signal.SIG_DFL,
            ):
                with mock.patch.object(
                    project_journal,
                    "_linux_sigchld_action",
                    side_effect=[
                        (0, 0),
                        (0, project_journal.LINUX_SA_NOCLDWAIT),
                    ],
                ):
                    project_journal._require_waitable_sigchld_semantics()
                    with mock.patch.object(
                        project_journal.os,
                        "killpg",
                    ) as killpg:
                        with self.assertRaises(
                            project_journal._ProcessIdentityLost
                        ) as raised:
                            project_journal._signal_process_group(
                                process,
                                signal.SIGTERM,
                            )

        self.assertIn(
            "waitable SIGCHLD semantics changed after process launch",
            str(raised.exception),
        )
        killpg.assert_not_called()

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "native Linux SA_NOCLDWAIT contract",
    )
    def test_linux_native_no_cldwait_never_launches_or_signals(self) -> None:
        machine = os.uname().machine.lower()
        multiarch = getattr(sys.implementation, "_multiarch", None)
        reviewed_multiarch = project_journal.LINUX_SIGACTION_REVIEWED_MULTIARCH.get(
            machine
        )
        if (
            ctypes.sizeof(ctypes.c_void_p) != 8
            or ctypes.sizeof(ctypes.c_ulong) != 8
            or reviewed_multiarch is None
            or multiarch not in reviewed_multiarch
        ):
            self.skipTest(f"unreviewed Linux sigaction ABI: {machine!r}, {multiarch!r}")
        driver = self.root / "linux-native-no-cldwait.py"
        driver.write_text(
            textwrap.dedent(
                """
                import ctypes
                import importlib.util
                import json
                import os
                import signal
                import sys

                script = sys.argv[1]
                spec = importlib.util.spec_from_file_location(
                    "linux_native_no_cldwait_project_journal",
                    script,
                )
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                library = ctypes.CDLL(None, use_errno=True)
                sigaction = library.sigaction
                sigaction.argtypes = (
                    ctypes.c_int,
                    ctypes.POINTER(module._LinuxSigaction),
                    ctypes.POINTER(module._LinuxSigaction),
                )
                sigaction.restype = ctypes.c_int
                sigismember = library.sigismember
                sigismember.argtypes = (
                    ctypes.POINTER(module._LinuxSigset),
                    ctypes.c_int,
                )
                sigismember.restype = ctypes.c_int

                def sigset_members(action):
                    # Compare semantic members, not libc sigset_t padding.
                    members = []
                    for signum in range(1, signal.NSIG):
                        membership = sigismember(
                            ctypes.byref(action.mask),
                            signum,
                        )
                        if membership < 0:
                            raise OSError(
                                ctypes.get_errno(),
                                f"failed to inspect signal-mask member {signum}",
                            )
                        if membership:
                            members.append(signum)
                    return members

                original = module._LinuxSigaction()
                if sigaction(
                    int(signal.SIGCHLD),
                    None,
                    ctypes.byref(original),
                ) != 0:
                    raise OSError(
                        ctypes.get_errno(),
                        "failed to read original SIGCHLD action",
                    )
                modified = module._LinuxSigaction.from_buffer_copy(original)
                modified.handler = None
                modified.flags |= module.LINUX_SA_NOCLDWAIT
                if sigaction(
                    int(signal.SIGCHLD),
                    ctypes.byref(modified),
                    None,
                ) != 0:
                    raise OSError(
                        ctypes.get_errno(),
                        "failed to set native SA_NOCLDWAIT",
                    )

                popen_calls = 0
                killpg_calls = 0
                auto_reaped = False
                rejection = None
                restored = None
                restore_result = None
                restored_waitable = False
                restored_wait_status = None
                try:
                    pid = os.fork()
                    if pid == 0:
                        os._exit(0)
                    try:
                        os.waitpid(pid, 0)
                    except ChildProcessError:
                        auto_reaped = True

                    def forbidden_popen(*args, **kwargs):
                        global popen_calls
                        popen_calls += 1
                        raise AssertionError(
                            "Popen must not run with native SA_NOCLDWAIT"
                        )

                    def forbidden_killpg(*args, **kwargs):
                        global killpg_calls
                        killpg_calls += 1
                        raise AssertionError(
                            "killpg must not run without a PID/PGID fence"
                        )

                    module.subprocess.Popen = forbidden_popen
                    module.os.killpg = forbidden_killpg
                    try:
                        module._capture_bounded_process(
                            [sys.executable, "-c", "raise SystemExit(7)"],
                            env={},
                            timeout_seconds=1,
                            stdout_limit=1024,
                            stderr_limit=1024,
                            stdout_overflow_error="stdout overflow",
                            stderr_overflow_error="stderr overflow",
                            timeout_error="timeout",
                        )
                    except module._WaitableSigchldUnavailable as exc:
                        rejection = str(exc)
                finally:
                    restore_result = sigaction(
                        int(signal.SIGCHLD),
                        ctypes.byref(original),
                        None,
                    )
                    restored = module._LinuxSigaction()
                    if restore_result == 0:
                        restore_result = sigaction(
                            int(signal.SIGCHLD),
                            None,
                            ctypes.byref(restored),
                        )
                    if restore_result == 0:
                        pid = os.fork()
                        if pid == 0:
                            os._exit(0)
                        try:
                            _, restored_wait_status = os.waitpid(pid, 0)
                        except ChildProcessError:
                            restored_waitable = False
                        else:
                            restored_waitable = True

                print(
                    json.dumps(
                        {
                            "auto_reaped": auto_reaped,
                            "killpg_calls": killpg_calls,
                            "popen_calls": popen_calls,
                            "rejection": rejection,
                            "restore_result": restore_result,
                            "restored_flags": (
                                restored.flags if restored is not None else None
                            ),
                            "restored_handler": (
                                int(restored.handler or 0)
                                if restored is not None
                                else None
                            ),
                            "restored_mask": (
                                sigset_members(restored)
                                if restored is not None
                                else None
                            ),
                            "restored_wait_status": restored_wait_status,
                            "restored_waitable": restored_waitable,
                            "original_flags": original.flags,
                            "original_handler": int(original.handler or 0),
                            "original_mask": sigset_members(original),
                        }
                    )
                )
                """
            ).lstrip(),
            encoding="utf-8",
        )

        result = subprocess.run(
            [sys.executable, str(driver), str(SCRIPT)],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertTrue(payload["auto_reaped"])
        self.assertEqual(payload["popen_calls"], 0)
        self.assertEqual(payload["killpg_calls"], 0)
        self.assertEqual(payload["restore_result"], 0)
        self.assertEqual(payload["restored_handler"], payload["original_handler"])
        self.assertEqual(payload["restored_mask"], payload["original_mask"])
        restored_flags = payload["restored_flags"] & 0xFFFFFFFF
        original_flags = payload["original_flags"] & 0xFFFFFFFF
        flag_delta = restored_flags ^ original_flags
        self.assertIn(flag_delta, (0, project_journal.LINUX_SA_RESTORER))
        self.assertEqual(
            restored_flags & project_journal.LINUX_SA_NOCLDWAIT,
            original_flags & project_journal.LINUX_SA_NOCLDWAIT,
        )
        self.assertTrue(payload["restored_waitable"])
        self.assertEqual(payload["restored_wait_status"], 0)
        self.assertIn("Linux SIGCHLD has SA_NOCLDWAIT", payload["rejection"])

    @unittest.skipUnless(
        sys.platform == "darwin"
        and project_journal._darwin_kqueue_status_observation_available(),
        "Darwin kqueue ESRCH waitability contract",
    )
    def test_darwin_kqueue_esrch_rejects_lost_sigchld_waitability(
        self,
    ) -> None:
        process = mock.Mock(spec=["pid"])
        process.pid = 12345
        queue = mock.Mock()
        queue.control.side_effect = ProcessLookupError(errno.ESRCH, "gone")

        with mock.patch.object(
            project_journal,
            "_posix_waitid_status_observation_available",
            return_value=False,
        ):
            with mock.patch.object(
                project_journal.select,
                "kqueue",
                return_value=queue,
            ):
                with mock.patch.object(
                    project_journal,
                    "_waitable_sigchld_failure",
                    side_effect=[
                        None,
                        "SIGCHLD became ignored and the child may be auto-reaped",
                    ],
                ):
                    with self.assertRaises(
                        project_journal._ProcessIdentityLost
                    ) as raised:
                        project_journal._register_process_status_observer(process)

        self.assertIn(
            "waitable SIGCHLD semantics changed after process launch",
            str(raised.exception),
        )
        queue.close.assert_called_once()
        self.assertFalse(hasattr(process, "_project_journal_status_observer"))

    @unittest.skipUnless(
        sys.platform == "darwin"
        and project_journal._darwin_kqueue_status_observation_available(),
        "Darwin kqueue child-status contract",
    )
    def test_darwin_kqueue_fallback_reaps_success_and_timeout_children(
        self,
    ) -> None:
        actual_popen = subprocess.Popen
        spawned: list[subprocess.Popen[bytes]] = []

        def capture_popen(
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            process = actual_popen(*args, **kwargs)
            spawned.append(process)
            return process

        with mock.patch.object(
            project_journal,
            "_posix_waitid_status_observation_available",
            return_value=False,
        ):
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=capture_popen,
            ):
                result = self.capture_process(
                    [sys.executable, "-c", "raise SystemExit(7)"],
                    timeout_seconds=5,
                    stdout_limit=1024,
                )
                with self.assertRaisesRegex(
                    project_journal.UserError,
                    "test process timed out",
                ):
                    self.capture_process(
                        [sys.executable, "-c", "import time; time.sleep(30)"],
                        timeout_seconds=0.05,
                        stdout_limit=1024,
                    )

        self.assertEqual(result.returncode, 7)
        self.assertEqual(len(spawned), 2)
        for process in spawned:
            self.assertIsInstance(process.returncode, int)
            with self.assertRaises(ChildProcessError):
                os.waitpid(process.pid, os.WNOHANG)

    def test_linux_group_member_proof_parses_live_nonleader_and_skips_zombies(
        self,
    ) -> None:
        pgid = 12345
        entries = mock.MagicMock()
        entry_names = (str(pgid), "12346", "12347", "self", "net")
        proc_entries = []
        for name in entry_names:
            entry = mock.Mock()
            entry.name = name
            proc_entries.append(entry)
        entries.__iter__.return_value = iter(proc_entries)
        stat_by_fd = {
            12346: b"12346 (zombie worker) Z 1 12345 12345 0 0 0\n",
            12347: b"12347 (live ) worker) S 1 12345 12345 0 0 0\n",
        }

        def open_stat(path: str, _flags: int) -> int:
            return int(path.removeprefix("/proc/").removesuffix("/stat"))

        with mock.patch.object(
            project_journal.os,
            "scandir",
            return_value=entries,
        ):
            with mock.patch.object(
                project_journal.os,
                "open",
                side_effect=open_stat,
            ) as open_file:
                with mock.patch.object(
                    project_journal.os,
                    "read",
                    side_effect=lambda fd, _limit: stat_by_fd[fd],
                ):
                    with mock.patch.object(project_journal.os, "close") as close_file:
                        with mock.patch.object(
                            project_journal.time,
                            "monotonic",
                            return_value=0.0,
                        ):
                            exists, error = (
                                project_journal._linux_process_group_has_live_nonleader(
                                    pgid,
                                    1.0,
                                )
                            )

        self.assertTrue(exists)
        self.assertIsNone(error)
        opened_paths = [call.args[0] for call in open_file.call_args_list]
        self.assertNotIn(f"/proc/{pgid}/stat", opened_paths)
        self.assertEqual(
            opened_paths,
            ["/proc/12346/stat", "/proc/12347/stat"],
        )
        self.assertEqual(
            [call.args[0] for call in close_file.call_args_list],
            [12346, 12347],
        )

    def test_linux_group_member_proof_deadline_keeps_group_present(self) -> None:
        entries = mock.MagicMock()
        entry = mock.Mock()
        entry.name = "12346"
        entries.__iter__.return_value = iter([entry])

        with mock.patch.object(
            project_journal.os,
            "scandir",
            return_value=entries,
        ):
            with mock.patch.object(
                project_journal.time,
                "monotonic",
                return_value=1.0,
            ):
                exists, error = project_journal._linux_process_group_has_live_nonleader(
                    12345,
                    1.0,
                )

        self.assertTrue(exists)
        self.assertIsNone(error)

    def test_linux_group_member_proof_reports_unreadable_proc_entry(self) -> None:
        entries = mock.MagicMock()
        entry = mock.Mock()
        entry.name = "12346"
        entries.__iter__.return_value = iter([entry])

        with mock.patch.object(
            project_journal.os,
            "scandir",
            return_value=entries,
        ):
            with mock.patch.object(
                project_journal.os,
                "open",
                side_effect=OSError(errno.EACCES, "injected unreadable stat"),
            ):
                with mock.patch.object(
                    project_journal.time,
                    "monotonic",
                    return_value=0.0,
                ):
                    exists, error = (
                        project_journal._linux_process_group_has_live_nonleader(
                            12345,
                            1.0,
                        )
                    )

        self.assertTrue(exists)
        self.assertIsNotNone(error)
        assert error is not None
        self.assertIn("failed to open Linux process metadata", error)
        self.assertIn("injected unreadable stat", error)

    @unittest.skipUnless(
        sys.platform.startswith("linux") and hasattr(os, "WNOWAIT"),
        "Linux WNOWAIT process-group member proof",
    )
    def test_linux_cleanup_ignores_exited_leader_without_descendants(self) -> None:
        actual_signal = project_journal._signal_process_group
        with mock.patch.object(
            project_journal,
            "_signal_process_group",
            wraps=actual_signal,
        ) as signal_group:
            result = self.capture_process(
                [sys.executable, "-c", "pass"],
                timeout_seconds=5,
                stdout_limit=1024,
            )

        self.assertEqual(result.returncode, 0)
        signal_group.assert_not_called()

    @unittest.skipUnless(
        sys.platform.startswith("linux") and hasattr(os, "WNOWAIT"),
        "Linux WNOWAIT process-group member proof",
    )
    def test_linux_cleanup_detects_and_kills_live_descendant(self) -> None:
        ready = self.root / "linux-group-child-ready"
        marker = self.root / "linux-group-child-survived"
        child = self.root / "linux-group-child.py"
        child.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import signal
                import sys
                import time

                signal.signal(signal.SIGTERM, signal.SIG_IGN)
                pathlib.Path(sys.argv[1]).write_text("ready", encoding="utf-8")
                time.sleep(1.5)
                pathlib.Path(sys.argv[2]).write_text("survived", encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        leader = self.root / "linux-group-leader.py"
        leader.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import subprocess
                import sys
                import time

                subprocess.Popen(
                    [sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                ready = pathlib.Path(sys.argv[2])
                deadline = time.monotonic() + 5
                while not ready.exists():
                    if time.monotonic() >= deadline:
                        raise SystemExit("descendant did not become ready")
                    time.sleep(0.005)
                """
            ),
            encoding="utf-8",
        )
        actual_signal = project_journal._signal_process_group
        with mock.patch.object(
            project_journal,
            "_signal_process_group",
            wraps=actual_signal,
        ) as signal_group:
            result = self.capture_process(
                [
                    sys.executable,
                    str(leader),
                    str(child),
                    str(ready),
                    str(marker),
                ],
                timeout_seconds=5,
                stdout_limit=1024,
            )

        self.assertEqual(result.returncode, 0)
        signals = [call.args[1] for call in signal_group.call_args_list]
        self.assertEqual(
            signals,
            [signal.SIGTERM, getattr(signal, "SIGKILL", signal.SIGTERM)],
        )
        time.sleep(1.6)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_restore_window_signal_is_waited_before_custom_propagation(
        self,
    ) -> None:
        handled = signal.SIGTERM
        ignored = signal.SIGHUP
        actual_signal = signal.signal
        original_handlers = {
            handled: signal.getsignal(handled),
            ignored: signal.getsignal(ignored),
        }
        original_mask = {
            int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        }
        events: list[str] = []
        signal_calls: list[int] = []
        injected = False
        stderr = io.StringIO()
        cleanup_issue = (
            "Git runtime snapshot cleanup-incomplete; retained locator "
            "/tmp/project-journal-signal-test"
        )

        def custom_handler(signum: int, _frame: object) -> None:
            self.assertEqual(signum, handled)
            events.append("custom-handler")

        def signal_with_restore_fault(
            signum: int,
            handler: object,
        ) -> object:
            nonlocal injected
            signal_calls.append(signum)
            previous = actual_signal(signum, handler)
            if signum == handled and handler is custom_handler and not injected:
                injected = True
                events.append("restore-handler")
                os.kill(os.getpid(), handled)
            return previous

        def cleanup_runtime() -> str:
            events.append("cleanup-runtime")
            return cleanup_issue

        actual_signal(handled, custom_handler)
        actual_signal(ignored, signal.SIG_IGN)
        try:
            with mock.patch.object(
                project_journal,
                "_termination_signals",
                return_value=(handled, ignored),
            ):
                with mock.patch.object(
                    project_journal.signal,
                    "signal",
                    side_effect=signal_with_restore_fault,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_cleanup_git_runtime_at_terminal",
                        side_effect=cleanup_runtime,
                    ) as cleanup:
                        with mock.patch.object(project_journal.sys, "stderr", stderr):
                            result = project_journal._run_with_deferred_termination(
                                lambda: 17
                            )
        finally:
            actual_signal(handled, original_handlers[handled])
            actual_signal(ignored, original_handlers[ignored])
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)

        self.assertTrue(injected)
        self.assertEqual(result, 128 + handled)
        cleanup.assert_called_once_with()
        self.assertNotIn(ignored, signal_calls)
        self.assertEqual(
            events,
            [
                "cleanup-runtime",
                "restore-handler",
                "custom-handler",
            ],
        )
        self.assertIn(cleanup_issue, stderr.getvalue())
        self.assertEqual(signal.getsignal(ignored), original_handlers[ignored])
        current_mask = {
            int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        }
        self.assertEqual(current_mask, original_mask)

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_final_check_to_unmask_signal_sees_terminal_cleanup(self) -> None:
        handled = signal.SIGTERM
        actual_pthread_sigmask = signal.pthread_sigmask
        original_handler = signal.getsignal(handled)
        original_mask = {
            int(value) for value in actual_pthread_sigmask(signal.SIG_BLOCK, set())
        }
        events: list[str] = []
        setmask_calls = 0

        def custom_handler(signum: int, _frame: object) -> None:
            self.assertEqual(signum, handled)
            events.append("custom-handler")

        def pthread_sigmask_with_final_window_signal(
            how: int,
            signals: set[int],
        ) -> set[signal.Signals]:
            nonlocal setmask_calls
            if how == signal.SIG_SETMASK:
                setmask_calls += 1
                if setmask_calls == 3:
                    events.append("final-window-signal")
                    os.kill(os.getpid(), handled)
            return actual_pthread_sigmask(how, signals)

        def cleanup_runtime() -> None:
            events.append("cleanup-runtime")

        signal.signal(handled, custom_handler)
        try:
            with mock.patch.object(
                project_journal,
                "_termination_signals",
                return_value=(handled,),
            ):
                with mock.patch.object(
                    project_journal.signal,
                    "pthread_sigmask",
                    side_effect=pthread_sigmask_with_final_window_signal,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_cleanup_git_runtime_at_terminal",
                        side_effect=cleanup_runtime,
                    ) as cleanup:
                        result = project_journal._run_with_deferred_termination(
                            lambda: 29
                        )
        finally:
            signal.signal(handled, original_handler)
            actual_pthread_sigmask(signal.SIG_SETMASK, original_mask)

        self.assertEqual(result, 29)
        cleanup.assert_called_once_with()
        self.assertEqual(
            events,
            [
                "cleanup-runtime",
                "final-window-signal",
                "custom-handler",
            ],
        )
        current_mask = {
            int(value) for value in actual_pthread_sigmask(signal.SIG_BLOCK, set())
        }
        self.assertEqual(current_mask, original_mask)

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_caller_blocked_signal_remains_blocked_and_pending(self) -> None:
        blocked = signal.SIGQUIT
        original_handler = signal.getsignal(blocked)
        original_mask = {
            int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        }
        delivered: list[int] = []
        runtime = project_journal._load_posix_signal_runtime()

        def custom_handler(signum: int, _frame: object) -> None:
            delivered.append(signum)

        current_mask: set[int] = set()
        pending: set[int] = set()
        signal.signal(blocked, custom_handler)
        signal.pthread_sigmask(signal.SIG_BLOCK, {blocked})
        try:
            with mock.patch.object(
                project_journal,
                "_termination_signals",
                return_value=(blocked,),
            ):
                with mock.patch.object(
                    project_journal,
                    "_cleanup_git_runtime_at_terminal",
                    return_value=None,
                ):
                    result = project_journal._run_with_deferred_termination(
                        lambda: os.kill(os.getpid(), blocked) or 31
                    )

            current_mask = {
                int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
            }
            pending = {int(value) for value in signal.sigpending()}
        finally:
            if blocked in {int(value) for value in signal.sigpending()}:
                runtime.sigwait({blocked})
            signal.signal(blocked, original_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)

        self.assertEqual(result, 31)
        self.assertEqual(delivered, [])
        self.assertIn(blocked, current_mask)
        self.assertIn(blocked, pending)

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_multiple_real_signals_propagate_only_the_first_managed_signal(
        self,
    ) -> None:
        first = signal.SIGTERM
        second = signal.SIGQUIT
        ignored = signal.SIGHUP
        original_handlers = {
            signum: signal.getsignal(signum) for signum in (first, second, ignored)
        }
        original_mask = {
            int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        }
        delivered: list[int] = []

        def custom_handler(signum: int, _frame: object) -> None:
            delivered.append(signum)

        def send_signals() -> int:
            os.kill(os.getpid(), first)
            os.kill(os.getpid(), second)
            os.kill(os.getpid(), ignored)
            return 23

        for signum in (first, second):
            signal.signal(signum, custom_handler)
        signal.signal(ignored, signal.SIG_IGN)
        try:
            with mock.patch.object(
                project_journal,
                "_cleanup_git_runtime_at_terminal",
                return_value=None,
            ):
                with mock.patch.object(project_journal.sys, "stderr", io.StringIO()):
                    result = project_journal._run_with_deferred_termination(
                        send_signals
                    )
        finally:
            for signum, handler in original_handlers.items():
                signal.signal(signum, handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)

        self.assertEqual(result, 128 + first)
        self.assertEqual(delivered, [first])
        current_mask = {
            int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        }
        self.assertEqual(current_mask, original_mask)

    def test_missing_signal_mask_or_wait_capability_fails_before_action(
        self,
    ) -> None:
        cases = ("pthread_sigmask", "sigpending", "sigwait")
        for missing in cases:
            with self.subTest(missing=missing):
                action = mock.Mock(return_value=17)
                cleanup = mock.Mock(return_value=None)
                if missing == "pthread_sigmask":
                    capability_patch = mock.patch.object(
                        project_journal.signal,
                        "pthread_sigmask",
                        None,
                    )
                    fallback_patch = mock.patch.object(
                        project_journal,
                        "_LibcSigwait",
                        wraps=project_journal._LibcSigwait,
                    )
                elif missing == "sigpending":
                    capability_patch = mock.patch.object(
                        project_journal.signal,
                        "sigpending",
                        None,
                    )
                    fallback_patch = mock.patch.object(
                        project_journal,
                        "_LibcSigwait",
                        wraps=project_journal._LibcSigwait,
                    )
                else:
                    capability_patch = mock.patch.object(
                        project_journal.signal,
                        "sigwait",
                        None,
                        create=True,
                    )
                    fallback_patch = mock.patch.object(
                        project_journal,
                        "_LibcSigwait",
                        side_effect=project_journal.UnsupportedPlatform(
                            "sigwait unavailable"
                        ),
                    )
                with capability_patch:
                    with fallback_patch:
                        with mock.patch.object(
                            project_journal,
                            "_cleanup_git_runtime_at_terminal",
                            cleanup,
                        ):
                            with self.assertRaises(project_journal.UnsupportedPlatform):
                                project_journal._run_with_deferred_termination(action)
                action.assert_not_called()
                cleanup.assert_not_called()

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_terminal_mask_failure_still_cleans_runtime_and_restores_state(
        self,
    ) -> None:
        handled = signal.SIGTERM
        actual_runtime = project_journal._load_posix_signal_runtime()
        original_handler = signal.getsignal(handled)
        original_mask = {
            int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        }
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        runtime = self.make_fake_git_runtime("terminal-mask-failure")
        runtime_locator = pathlib.Path(runtime.snapshot_owner.name)
        events: list[str] = []
        managed_block_attempts = 0

        def custom_handler(_signum: int, _frame: object) -> None:
            events.append("custom-handler")

        def fail_first_terminal_block(
            how: int,
            signals: set[int],
        ) -> set[int]:
            nonlocal managed_block_attempts
            if how == actual_runtime.sig_block and signals == {handled}:
                managed_block_attempts += 1
                if managed_block_attempts == 2:
                    events.append("terminal-mask-failed")
                    raise project_journal.UnsupportedPlatform("terminal block failed")
                if managed_block_attempts == 3:
                    events.append("terminal-mask-retry")
            return actual_runtime.pthread_sigmask(how, signals)

        wrapped_runtime = project_journal._PosixSignalRuntime(
            pthread_sigmask=fail_first_terminal_block,
            sigpending=actual_runtime.sigpending,
            sigwait=actual_runtime.sigwait,
            sig_block=actual_runtime.sig_block,
            sig_setmask=actual_runtime.sig_setmask,
        )
        actual_cleanup = project_journal._cleanup_git_runtime_at_terminal

        def cleanup_runtime() -> str | None:
            events.append("runtime-cleanup")
            return actual_cleanup()

        action = mock.Mock(return_value=17)
        signal.signal(handled, custom_handler)
        project_journal._GIT_RUNTIME = runtime
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal,
                "_load_posix_signal_runtime",
                return_value=wrapped_runtime,
            ):
                with mock.patch.object(
                    project_journal,
                    "_termination_signals",
                    return_value=(handled,),
                ):
                    with mock.patch.object(
                        project_journal,
                        "_cleanup_git_runtime_at_terminal",
                        side_effect=cleanup_runtime,
                    ) as cleanup:
                        with self.assertRaisesRegex(
                            project_journal.UnsupportedPlatform,
                            "terminal block failed",
                        ):
                            project_journal._run_with_deferred_termination(action)

            action.assert_called_once_with()
            cleanup.assert_called_once_with()
            self.assertEqual(
                events,
                [
                    "terminal-mask-failed",
                    "runtime-cleanup",
                    "terminal-mask-retry",
                ],
            )
            self.assertFalse(runtime_locator.exists())
            self.assertIsNone(project_journal._GIT_RUNTIME)
            self.assertIsNone(project_journal._ACTIVE_DEFERRED_TERMINATION)
            self.assertIs(signal.getsignal(handled), custom_handler)
            current_mask = {
                int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
            }
            self.assertEqual(current_mask, original_mask)
        finally:
            if runtime_locator.exists():
                runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error
            signal.signal(handled, original_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_persistent_terminal_mask_failure_uses_best_safe_restoration(
        self,
    ) -> None:
        handled = signal.SIGTERM
        actual_runtime = project_journal._load_posix_signal_runtime()
        original_handler = signal.getsignal(handled)
        original_mask = {
            int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        }
        managed_block_attempts = 0

        def custom_handler(_signum: int, _frame: object) -> None:
            raise AssertionError("unexpected signal delivery")

        def fail_terminal_blocks(
            how: int,
            signals: set[int],
        ) -> set[int]:
            nonlocal managed_block_attempts
            if how == actual_runtime.sig_block and signals == {handled}:
                managed_block_attempts += 1
                if managed_block_attempts >= 2:
                    raise project_journal.UnsupportedPlatform(
                        "persistent terminal block failure"
                    )
            return actual_runtime.pthread_sigmask(how, signals)

        wrapped_runtime = project_journal._PosixSignalRuntime(
            pthread_sigmask=fail_terminal_blocks,
            sigpending=actual_runtime.sigpending,
            sigwait=actual_runtime.sigwait,
            sig_block=actual_runtime.sig_block,
            sig_setmask=actual_runtime.sig_setmask,
        )

        signal.signal(handled, custom_handler)
        try:
            with mock.patch.object(
                project_journal,
                "_load_posix_signal_runtime",
                return_value=wrapped_runtime,
            ):
                with mock.patch.object(
                    project_journal,
                    "_termination_signals",
                    return_value=(handled,),
                ):
                    with mock.patch.object(
                        project_journal,
                        "_cleanup_git_runtime_at_terminal",
                        return_value=None,
                    ) as cleanup:
                        with self.assertRaisesRegex(
                            project_journal.UnsupportedPlatform,
                            "persistent terminal block failure",
                        ):
                            project_journal._run_with_deferred_termination(lambda: 19)

            cleanup.assert_called_once_with()
            self.assertEqual(managed_block_attempts, 3)
            self.assertIsNone(project_journal._ACTIVE_DEFERRED_TERMINATION)
            self.assertIs(signal.getsignal(handled), custom_handler)
            current_mask = {
                int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
            }
            self.assertEqual(current_mask, original_mask)
        finally:
            signal.signal(handled, original_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_main_reports_cleanup_locator_without_exception_add_note(
        self,
    ) -> None:
        handled = signal.SIGTERM
        wrapped_runtime, managed_block_attempts = (
            self.make_terminal_mask_failure_runtime(
                handled,
                persistent=False,
            )
        )
        original_handler = signal.getsignal(handled)
        original_mask = {
            int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        }
        cleanup_issue = (
            "Git runtime snapshot cleanup-incomplete; retained locator "
            "/tmp/project-journal-command-error"
        )
        parser = mock.Mock()

        def fail_command(_args: object) -> int:
            raise project_journal.UserError("command rejected")

        parser.parse_args.return_value = mock.Mock(func=fail_command)
        stderr = io.StringIO()
        try:
            with mock.patch.object(
                project_journal.UserError,
                "add_note",
                None,
                create=True,
            ):
                with mock.patch.object(
                    project_journal,
                    "_load_posix_signal_runtime",
                    return_value=wrapped_runtime,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_termination_signals",
                        return_value=(handled,),
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_initialize_git_runtime",
                            return_value=None,
                        ):
                            with mock.patch.object(
                                project_journal,
                                "build_parser",
                                return_value=parser,
                            ):
                                with mock.patch.object(
                                    project_journal,
                                    "_cleanup_git_runtime_at_terminal",
                                    return_value=cleanup_issue,
                                ) as cleanup:
                                    with mock.patch.object(
                                        project_journal.sys,
                                        "stderr",
                                        stderr,
                                    ):
                                        status = project_journal.main([])
        finally:
            signal.signal(handled, original_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)

        self.assertEqual(status, 1)
        self.assertEqual(managed_block_attempts, [3])
        cleanup.assert_called_once_with()
        parser.parse_args.assert_called_once_with([])
        self.assertIn("error: command rejected", stderr.getvalue())
        self.assertIn(
            "note: terminal convergence failed: terminal block failure",
            stderr.getvalue(),
        )
        self.assertIn(f"note: {cleanup_issue}", stderr.getvalue())

    def test_pending_signal_report_reserves_cleanup_locator_slot(self) -> None:
        state = project_journal._DeferredTerminationState(
            pending_signal=signal.SIGTERM,
        )
        interruption = LegacyUnsupportedPlatform("action failed")
        interruption.__notes__ = [f"action note {index}" for index in range(7)]
        cleanup_issue = (
            "Git runtime snapshot cleanup-incomplete; retained locator "
            "/tmp/project-journal-saturated-report"
        )
        stderr = io.StringIO()

        with mock.patch.object(project_journal.sys, "stderr", stderr):
            project_journal._report_deferred_termination(
                state,
                interruption,
                cleanup_issue,
            )

        note_lines = [
            line for line in stderr.getvalue().splitlines() if line.startswith("note: ")
        ]
        self.assertEqual(
            len(note_lines),
            project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS,
        )
        self.assertEqual(note_lines[0], "note: action failed")
        self.assertIn("note: action note 5", note_lines)
        self.assertNotIn("note: action note 6", note_lines)
        self.assertEqual(note_lines[-1], f"note: {cleanup_issue}")

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_action_exception_remains_primary_after_persistent_terminal_failure(
        self,
    ) -> None:
        handled = signal.SIGTERM
        wrapped_runtime, managed_block_attempts = (
            self.make_terminal_mask_failure_runtime(
                handled,
                persistent=True,
            )
        )
        original_handler = signal.getsignal(handled)
        original_mask = {
            int(value) for value in signal.pthread_sigmask(signal.SIG_BLOCK, set())
        }
        cleanup_issue = (
            "Git runtime snapshot cleanup-incomplete; retained locator "
            "/tmp/project-journal-persistent-terminal"
        )
        action_error = ValueError("action failed")
        action_error.__notes__ = [
            f"action recovery note {index}"
            for index in range(project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS)
        ]

        def fail_action() -> int:
            raise action_error

        try:
            with mock.patch.object(
                project_journal,
                "_load_posix_signal_runtime",
                return_value=wrapped_runtime,
            ):
                with mock.patch.object(
                    project_journal,
                    "_termination_signals",
                    return_value=(handled,),
                ):
                    with mock.patch.object(
                        project_journal,
                        "_cleanup_git_runtime_at_terminal",
                        return_value=cleanup_issue,
                    ) as cleanup:
                        with self.assertRaises(ValueError) as raised:
                            project_journal._run_with_deferred_termination(fail_action)
        finally:
            signal.signal(handled, original_handler)
            signal.pthread_sigmask(signal.SIG_SETMASK, original_mask)

        self.assertIs(raised.exception, action_error)
        self.assertEqual(managed_block_attempts, [3])
        cleanup.assert_called_once_with()
        action_notes = getattr(raised.exception, "__notes__", ())
        self.assertLessEqual(
            len(action_notes),
            project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS,
        )
        retained_action_notes = project_journal.MAX_DEFERRED_SIGNAL_REPORT_DETAILS - 3
        self.assertEqual(
            action_notes[:retained_action_notes],
            [f"action recovery note {index}" for index in range(retained_action_notes)],
        )
        self.assertEqual(action_notes[-1], cleanup_issue)
        notes = "\n".join(action_notes)
        self.assertIn("action recovery note", notes)
        self.assertIn(
            "terminal convergence failed: persistent terminal block failure",
            notes,
        )
        self.assertIn(
            "terminal convergence also failed: persistent terminal block failure",
            notes,
        )
        self.assertIn(cleanup_issue, notes)

    @unittest.skipUnless(os.name == "posix", "POSIX Git runtime contract")
    def test_terminal_runtime_cleanup_exception_returns_bounded_issue(
        self,
    ) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        runtime = self.make_fake_git_runtime("terminal-cleanup-exception")
        locator = pathlib.Path(runtime.snapshot_owner.name)
        cleanup_error = OSError(
            errno.EIO,
            "simulated terminal runtime cleanup failure",
        )
        expected_issue = (
            "Git runtime snapshot cleanup-incomplete; retained locator "
            f"{locator}: {cleanup_error}"
        )

        project_journal._GIT_RUNTIME = runtime
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                runtime.snapshot_owner,
                "cleanup",
                side_effect=cleanup_error,
            ) as cleanup:
                issue = project_journal._cleanup_git_runtime_at_terminal()

            self.assertEqual(issue, expected_issue)
            cleanup.assert_called_once_with()
            self.assertIs(project_journal._GIT_RUNTIME, runtime)
            self.assertIsInstance(
                project_journal._GIT_RUNTIME_ERROR,
                project_journal.UnsupportedGitVersion,
            )
            assert project_journal._GIT_RUNTIME_ERROR is not None
            self.assertEqual(str(project_journal._GIT_RUNTIME_ERROR), expected_issue)
            self.assertTrue(locator.exists())
            self.assertEqual(getattr(cleanup_error, "__notes__", ()), ())
        finally:
            runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_terminal_cleanup_baseexception_remains_primary_without_action_error(
        self,
    ) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        runtime = self.make_fake_git_runtime("terminal-cleanup-interrupt")
        locator = pathlib.Path(runtime.snapshot_owner.name)
        cleanup_error = KeyboardInterrupt(
            "simulated terminal cleanup interruption "
            + "x" * (project_journal.MAX_DEFERRED_SIGNAL_REPORT_CHARS + 256)
        )
        original_args = cleanup_error.args
        action = mock.Mock(return_value=37)

        def interrupt_cleanup() -> None:
            raise cleanup_error

        project_journal._GIT_RUNTIME = runtime
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal,
                "_termination_signals",
                return_value=(),
            ):
                with mock.patch.object(
                    runtime.snapshot_owner,
                    "cleanup",
                    side_effect=interrupt_cleanup,
                ) as cleanup:
                    try:
                        project_journal._run_with_deferred_termination(action)
                    except KeyboardInterrupt as exc:
                        raised_error = exc
                    else:
                        self.fail("expected terminal cleanup interruption")

            self.assertIs(raised_error, cleanup_error)
            self.assertIs(type(raised_error), KeyboardInterrupt)
            self.assertEqual(raised_error.args, original_args)
            action.assert_called_once_with()
            cleanup.assert_called_once_with()
            notes = getattr(raised_error, "__notes__", ())
            self.assertEqual(len(notes), 1)
            self.assertIn(str(locator), notes[0])
            self.assertTrue(notes[0].endswith("…[truncated]"))
            self.assertLessEqual(
                len(notes[0]),
                project_journal.MAX_DEFERRED_SIGNAL_REPORT_CHARS + len("…[truncated]"),
            )
            traceback_names = self.exception_traceback_names(raised_error)
            self.assertIn("interrupt_cleanup", traceback_names)
            self.assertIn("_cleanup_git_runtime_at_terminal", traceback_names)
            self.assertIn("_run_with_deferred_termination", traceback_names)
            self.assertIs(project_journal._GIT_RUNTIME, runtime)
            self.assertIsInstance(
                project_journal._GIT_RUNTIME_ERROR,
                project_journal.UnsupportedGitVersion,
            )
            assert project_journal._GIT_RUNTIME_ERROR is not None
            self.assertEqual(str(project_journal._GIT_RUNTIME_ERROR), notes[0])
            self.assertTrue(locator.exists())
            self.assertIsNone(project_journal._ACTIVE_DEFERRED_TERMINATION)
        finally:
            runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_action_error_remains_primary_when_terminal_cleanup_exits(
        self,
    ) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        runtime = self.make_fake_git_runtime("terminal-cleanup-system-exit")
        locator = pathlib.Path(runtime.snapshot_owner.name)
        action_error = ValueError("simulated action failure")
        action_args = action_error.args
        cleanup_error = SystemExit("simulated terminal cleanup exit")

        def fail_action() -> int:
            raise action_error

        def exit_cleanup() -> None:
            raise cleanup_error

        project_journal._GIT_RUNTIME = runtime
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal,
                "_termination_signals",
                return_value=(),
            ):
                with mock.patch.object(
                    runtime.snapshot_owner,
                    "cleanup",
                    side_effect=exit_cleanup,
                ) as cleanup:
                    try:
                        project_journal._run_with_deferred_termination(fail_action)
                    except ValueError as exc:
                        raised_error = exc
                    else:
                        self.fail("expected active action failure")

            self.assertIs(raised_error, action_error)
            self.assertIs(type(raised_error), ValueError)
            self.assertEqual(raised_error.args, action_args)
            cleanup.assert_called_once_with()
            notes = "\n".join(getattr(raised_error, "__notes__", ()))
            self.assertIn(
                "terminal convergence failed: simulated terminal cleanup exit",
                notes,
            )
            self.assertIn(
                "Git runtime snapshot cleanup-incomplete; retained locator",
                notes,
            )
            self.assertIn(str(locator), notes)
            action_traceback_names = self.exception_traceback_names(raised_error)
            self.assertIn("fail_action", action_traceback_names)
            self.assertNotIn("exit_cleanup", action_traceback_names)
            cleanup_traceback_names = self.exception_traceback_names(cleanup_error)
            self.assertIn("exit_cleanup", cleanup_traceback_names)
            cleanup_notes = getattr(cleanup_error, "__notes__", ())
            self.assertEqual(len(cleanup_notes), 1)
            self.assertIn(str(locator), cleanup_notes[0])
            self.assertIs(project_journal._GIT_RUNTIME, runtime)
            self.assertTrue(locator.exists())
            self.assertIsNone(project_journal._ACTIVE_DEFERRED_TERMINATION)
        finally:
            runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

    def test_normal_main_invocations_reinitialize_after_terminal_cleanup(
        self,
    ) -> None:
        repo = self.init_repo()
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        stdout = io.StringIO()
        stderr = io.StringIO()
        try:
            with mock.patch.object(project_journal.sys, "stdout", stdout):
                with mock.patch.object(project_journal.sys, "stderr", stderr):
                    first = project_journal.main(
                        ["adoption-status", "--repo", str(repo)]
                    )
                    self.assertIsNone(project_journal._GIT_RUNTIME)
                    second = project_journal.main(
                        ["adoption-status", "--repo", str(repo)]
                    )
                    self.assertIsNone(project_journal._GIT_RUNTIME)
        finally:
            runtime = project_journal._GIT_RUNTIME
            if runtime is not None and runtime is not old_runtime:
                runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

        self.assertEqual((first, second), (0, 0), stderr.getvalue())
        self.assertEqual(stdout.getvalue().count('"tracked_journal_adopted": false'), 2)

    def test_help_avoids_cleanup_failure_prone_runtime_initialization(self) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        runtime = mock.Mock()
        runtime.snapshot_owner.name = str(self.root / "help-runtime")
        runtime.snapshot_owner.cleanup.side_effect = OSError(
            "simulated help runtime cleanup failure"
        )
        stdout = io.StringIO()
        stderr = io.StringIO()

        def initialize_failure_prone_runtime() -> None:
            project_journal._GIT_RUNTIME = runtime

        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        try:
            with mock.patch.object(
                project_journal,
                "_initialize_git_runtime",
                side_effect=initialize_failure_prone_runtime,
            ) as initialize:
                with mock.patch.object(project_journal.sys, "stdout", stdout):
                    with mock.patch.object(project_journal.sys, "stderr", stderr):
                        with self.assertRaises(SystemExit) as raised:
                            project_journal.main(["--help"])
        finally:
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

        self.assertEqual(raised.exception.code, 0)
        self.assertEqual(getattr(raised.exception, "__notes__", ()), ())
        initialize.assert_not_called()
        runtime.snapshot_owner.cleanup.assert_not_called()
        self.assertIn("usage:", stdout.getvalue())
        self.assertNotIn("retained locator", stderr.getvalue())

    @unittest.skipUnless(os.name == "posix", "POSIX Git runtime contract")
    def test_failed_runtime_initialization_can_retry_after_repair(self) -> None:
        repo = self.init_repo()
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        self.assertIsNotNone(old_runtime)
        assert old_runtime is not None
        project_journal._GIT_RUNTIME = None
        project_journal._GIT_RUNTIME_ERROR = None
        stdout = io.StringIO()
        stderr = io.StringIO()
        which_calls: list[str] = []

        def fail_then_find_git(
            command: str,
            path: str | None = None,
        ) -> str | None:
            del path
            which_calls.append(command)
            if len(which_calls) == 1:
                return None
            return str(old_runtime.source_executable)

        try:
            with mock.patch.object(
                project_journal.shutil,
                "which",
                side_effect=fail_then_find_git,
            ):
                with mock.patch.object(project_journal.sys, "stdout", stdout):
                    with mock.patch.object(project_journal.sys, "stderr", stderr):
                        first = project_journal.main(
                            ["adoption-status", "--repo", str(repo)]
                        )
                        self.assertIsNone(project_journal._GIT_RUNTIME)
                        self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
                        second = project_journal.main(
                            ["adoption-status", "--repo", str(repo)]
                        )
                        self.assertIsNone(project_journal._GIT_RUNTIME)
                        self.assertIsNone(project_journal._GIT_RUNTIME_ERROR)
        finally:
            runtime = project_journal._GIT_RUNTIME
            if runtime is not None and runtime is not old_runtime:
                runtime.snapshot_owner.cleanup()
            project_journal._GIT_RUNTIME = old_runtime
            project_journal._GIT_RUNTIME_ERROR = old_error

        self.assertEqual((first, second), (1, 0), stderr.getvalue())
        self.assertEqual(which_calls, ["git", "git"])
        self.assertIn("no Git executable was found on PATH", stderr.getvalue())
        self.assertIn('"tracked_journal_adopted": false', stdout.getvalue())

    @unittest.skipUnless(
        os.name == "posix"
        and hasattr(signal, "pthread_sigmask")
        and hasattr(signal, "sigpending"),
        "POSIX deferred termination mask contract",
    )
    def test_default_signal_propagates_only_after_terminal_cleanup(self) -> None:
        marker = self.root / "default-signal-cleanup"
        driver = self.root / "default-signal-driver.py"
        driver.write_text(
            textwrap.dedent(
                """\
                import importlib.util
                import os
                import pathlib
                import signal
                import sys

                script_text, marker_text = sys.argv[1:]
                spec = importlib.util.spec_from_file_location(
                    "project_journal_default_signal_driver",
                    script_text,
                )
                assert spec is not None
                assert spec.loader is not None
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)

                def cleanup():
                    pathlib.Path(marker_text).write_text(
                        "terminal",
                        encoding="utf-8",
                    )
                    return None

                def request_signal():
                    os.kill(os.getpid(), signal.SIGTERM)
                    return 19

                signal.signal(signal.SIGTERM, signal.SIG_DFL)
                module._termination_signals = lambda: (signal.SIGTERM,)
                module._cleanup_git_runtime_at_terminal = cleanup
                module._run_with_deferred_termination(request_signal)
                raise SystemExit("default signal did not terminate")
                """
            ),
            encoding="utf-8",
        )

        process = subprocess.run(
            [
                sys.executable,
                str(driver),
                str(SCRIPT),
                str(marker),
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=8,
            env={
                "PATH": os.environ.get("PATH", ""),
                "PYTHONDONTWRITEBYTECODE": "1",
            },
        )

        self.assertEqual(process.returncode, -signal.SIGTERM, process.stderr)
        self.assertEqual(marker.read_text(encoding="utf-8"), "terminal")
        self.assertIn("received SIGTERM", process.stderr)

    @unittest.skipUnless(
        os.name == "posix"
        and all(hasattr(signal, name) for name in ("SIGHUP", "SIGTERM", "SIGQUIT")),
        "POSIX deferred termination contract",
    )
    def test_helper_defers_terminal_signals_until_git_group_cleanup(self) -> None:
        for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGQUIT):
            with self.subTest(signal=signal.Signals(signum).name):
                case = self.root / f"deferred-{signal.Signals(signum).name.lower()}"
                fake_bin = case / "bin"
                fake_bin.mkdir(parents=True)
                temp_root = case / "tmp"
                temp_root.mkdir()
                ready = case / "descendant.ready"
                survived = case / "descendant.survived"
                fake_git = fake_bin / "git"
                fake_git.write_text(
                    f"#!{sys.executable}\n"
                    f"PJ_SIGNAL_READY = {str(ready)!r}\n"
                    f"PJ_SIGNAL_SURVIVED = {str(survived)!r}\n"
                    + textwrap.dedent(
                        """\
                        import os
                        import pathlib
                        import signal
                        import subprocess
                        import sys
                        import time

                        if sys.argv[1:] == ["--version"]:
                            print("git version 2.45.1")
                            raise SystemExit(0)

                        if sys.argv[1:] == ["--project-journal-descendant"]:
                            signal.signal(signal.SIGTERM, signal.SIG_IGN)
                            pathlib.Path(PJ_SIGNAL_READY).write_text(
                                str(os.getpid()),
                                encoding="utf-8",
                            )
                            time.sleep(0.75)
                            pathlib.Path(PJ_SIGNAL_SURVIVED).write_text(
                                "survived",
                                encoding="utf-8",
                            )
                            raise SystemExit(0)

                        subprocess.Popen(
                            [sys.executable, __file__, "--project-journal-descendant"],
                            stdin=subprocess.DEVNULL,
                        )
                        deadline = time.monotonic() + 5
                        ready = pathlib.Path(PJ_SIGNAL_READY)
                        while not ready.exists():
                            if time.monotonic() >= deadline:
                                raise SystemExit("descendant did not become ready")
                            time.sleep(0.005)
                        time.sleep(30)
                        """
                    ),
                    encoding="utf-8",
                )
                fake_git.chmod(0o755)
                driver = case / "signal-git-driver.py"
                driver.write_text(
                    textwrap.dedent(
                        """\
                        import importlib.util
                        import pathlib
                        import sys
                        import time

                        repo, source_text, script_text = sys.argv[1:]
                        spec = importlib.util.spec_from_file_location(
                            "project_journal_signal_git_driver",
                            script_text,
                        )
                        assert spec is not None
                        assert spec.loader is not None
                        module = importlib.util.module_from_spec(spec)
                        sys.modules[spec.name] = module
                        spec.loader.exec_module(module)
                        old_runtime = module._GIT_RUNTIME
                        if old_runtime is not None:
                            old_runtime.snapshot_owner.cleanup()
                        source = pathlib.Path(source_text)
                        (
                            snapshot,
                            digest,
                            snapshot_identity,
                            directory_identity,
                            _launcher_kind,
                            snapshot_owner,
                        ) = module._snapshot_git_executable(
                            source,
                            expected_source_identity=module._git_source_identity(
                                source.stat()
                            ),
                            deadline=time.monotonic() + 5,
                        )
                        module._GIT_RUNTIME = module._GitRuntime(
                            executable=snapshot,
                            source_executable=source,
                            launcher_kind="test-script",
                            version=(2, 45, 1),
                            digest=digest,
                            file_identity=snapshot_identity,
                            directory_identity=directory_identity,
                            snapshot_owner=snapshot_owner,
                        )
                        module._GIT_RUNTIME_ERROR = None
                        raise SystemExit(
                            module.main(
                                ["adoption-status", "--repo", repo]
                            )
                        )
                        """
                    ),
                    encoding="utf-8",
                )
                env = {
                    "PATH": os.environ.get("PATH", ""),
                    "HOME": str(self.home),
                    "GIT_CONFIG_NOSYSTEM": "1",
                    "PYTHONDONTWRITEBYTECODE": "1",
                    "TMPDIR": str(temp_root),
                }
                process = subprocess.Popen(
                    [
                        sys.executable,
                        str(driver),
                        str(case),
                        str(fake_git),
                        str(SCRIPT),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    env=env,
                )
                try:
                    deadline = time.monotonic() + 8
                    while not ready.exists():
                        if process.poll() is not None:
                            stdout, stderr = process.communicate()
                            self.fail(
                                "helper exited before its Git descendant was ready: "
                                f"stdout={stdout!r} stderr={stderr!r}"
                            )
                        if time.monotonic() >= deadline:
                            self.fail("helper Git descendant did not become ready")
                        time.sleep(0.01)

                    process.send_signal(signum)
                    stdout, stderr = process.communicate(timeout=8)
                    self.assertEqual(stdout, "")
                    self.assertEqual(process.returncode, -signum, stderr)
                    self.assertIn(
                        f"received {signal.Signals(signum).name}",
                        stderr,
                    )
                    self.assertIn(
                        "protected cleanup reached a terminal state",
                        stderr,
                    )
                    time.sleep(0.9)
                    self.assertFalse(
                        survived.exists(),
                        "launch-owned descendant survived deferred cleanup",
                    )
                    self.assertEqual(
                        list(temp_root.glob("project-journal-git-runtime-*")),
                        [],
                    )
                    self.assertEqual(
                        list(temp_root.glob("project-journal-git-launch-*")),
                        [],
                    )
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.communicate()
                    if ready.exists():
                        try:
                            os.kill(
                                int(ready.read_text(encoding="utf-8")), signal.SIGKILL
                            )
                        except (ProcessLookupError, ValueError):
                            pass

    @unittest.skipUnless(
        os.name == "posix" and hasattr(signal, "SIGHUP"),
        "POSIX deferred termination contract",
    )
    def test_helper_reports_rename_state_before_propagating_signal(self) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        ready = self.root / "rename.ready"
        release = self.root / "rename.release"
        driver = self.root / "signal-rename-driver.py"
        driver.write_text(
            textwrap.dedent(
                """\
                import importlib.util
                import pathlib
                import sys
                import time

                repo, ready_text, release_text, script_text = sys.argv[1:]
                spec = importlib.util.spec_from_file_location(
                    "project_journal_signal_driver",
                    script_text,
                )
                assert spec is not None
                assert spec.loader is not None
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                spec.loader.exec_module(module)
                actual_rename = module._rename_hook_entry_with_flag
                ready = pathlib.Path(ready_text)
                release = pathlib.Path(release_text)

                def rename_then_wait(
                    directory_fd,
                    source,
                    destination,
                    *,
                    exchange,
                ):
                    actual_rename(
                        directory_fd,
                        source,
                        destination,
                        exchange=exchange,
                    )
                    if destination != "post-merge" or exchange:
                        return
                    ready.write_text("renamed", encoding="utf-8")
                    deadline = time.monotonic() + 8
                    while not release.exists():
                        if time.monotonic() >= deadline:
                            raise RuntimeError("signal release was not provided")
                        time.sleep(0.005)

                module._rename_hook_entry_with_flag = rename_then_wait
                raise SystemExit(
                    module.main(["install-hooks", "--repo", repo])
                )
                """
            ),
            encoding="utf-8",
        )
        env = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        process = subprocess.Popen(
            [
                sys.executable,
                str(driver),
                str(repo),
                str(ready),
                str(release),
                str(SCRIPT),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        try:
            deadline = time.monotonic() + 8
            while not ready.exists():
                if process.poll() is not None:
                    stdout, stderr = process.communicate()
                    self.fail(
                        "helper exited before the no-replace rename: "
                        f"stdout={stdout!r} stderr={stderr!r}"
                    )
                if time.monotonic() >= deadline:
                    self.fail("helper did not reach the no-replace rename")
                time.sleep(0.01)

            process.send_signal(signal.SIGHUP)
            release.write_text("continue", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=8)
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

        self.assertEqual(stdout, "")
        self.assertEqual(process.returncode, -signal.SIGHUP, stderr)
        self.assertIn("received SIGHUP", stderr)
        self.assertIn("absent-target no-replace rename committed", stderr)
        self.assertIn(
            "object-identity/content/access-policy verified",
            stderr,
        )
        self.assertIn("no displaced-hook recovery object exists", stderr)
        self.assertNotIn("preserved recovery locator", stderr)
        self.assertTrue((repo / ".githooks/post-merge").exists())
        self.assertEqual(
            list((repo / ".githooks").glob(".project-journal-post-merge-*.tmp")),
            [],
        )

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_group_cleanup_keeps_leader_fence_and_orders_final_signal(
        self,
    ) -> None:
        kill_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        for initial_group_exists, group_exists, expected_signals in (
            (False, False, []),
            (True, False, [signal.SIGTERM]),
            (True, True, [signal.SIGTERM, kill_signal]),
        ):
            with self.subTest(
                initial_group_exists=initial_group_exists,
                group_exists=group_exists,
            ):
                events: list[tuple[str, int | None]] = []
                process = mock.Mock()
                process.pid = 12345
                process.poll.side_effect = AssertionError(
                    "poll must not reap the fence"
                )

                def wait_after_signals(*, timeout: float) -> int:
                    self.assertGreaterEqual(timeout, 0)
                    events.append(("wait", None))
                    return -signal.SIGTERM

                process.wait.side_effect = wait_after_signals
                selector = mock.Mock()
                probe_calls = 0

                def signal_group(
                    process_arg: subprocess.Popen[bytes],
                    sig: int,
                ) -> project_journal._ProcessSignalResult:
                    self.assertIs(process_arg, process)
                    events.append(("signal", sig))
                    return project_journal._ProcessSignalResult(target_existed=True)

                def initial_probe(
                    process_arg: subprocess.Popen[bytes],
                    *,
                    leader_exited: bool,
                    deadline: float | None,
                ) -> tuple[bool, None]:
                    self.assertIs(process_arg, process)
                    self.assertFalse(leader_exited)
                    self.assertIsNotNone(deadline)
                    events.append(("initial-probe", None))
                    return initial_group_exists, None

                def probe_group(
                    process_arg: subprocess.Popen[bytes],
                    deadline: float,
                    *,
                    leader_exited: bool,
                ) -> tuple[bool, None]:
                    nonlocal probe_calls
                    self.assertIs(process_arg, process)
                    self.assertGreater(deadline, 0)
                    if probe_calls == 0:
                        self.assertFalse(leader_exited)
                    else:
                        self.assertTrue(leader_exited)
                    probe_calls += 1
                    events.append(
                        (
                            "post-term-probe"
                            if probe_calls == 1
                            else "post-kill-probe",
                            None,
                        )
                    )
                    return (group_exists if probe_calls == 1 else False), None

                def observe_status(
                    process_arg: subprocess.Popen[bytes],
                    deadline: float,
                    timeout_error: str,
                    *,
                    interruptible: bool,
                ) -> int:
                    self.assertIs(process_arg, process)
                    self.assertGreater(deadline, 0)
                    self.assertTrue(timeout_error)
                    self.assertFalse(interruptible)
                    events.append(("status", None))
                    return -signal.SIGTERM

                with mock.patch.object(
                    project_journal,
                    "_signal_process_group",
                    side_effect=signal_group,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_bound_process_group_exists",
                        side_effect=initial_probe,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_discard_selector_output",
                        ):
                            with mock.patch.object(
                                project_journal,
                                "_wait_for_bound_process_group_absence",
                                side_effect=probe_group,
                            ):
                                with mock.patch.object(
                                    project_journal,
                                    "_close_selector",
                                ):
                                    with mock.patch.object(
                                        project_journal,
                                        "_wait_for_process_status_without_reaping",
                                        side_effect=observe_status,
                                    ):
                                        cleanup_error = project_journal._terminate_process_group_and_reap(
                                            process,
                                            selector,
                                        )

                self.assertIsNone(cleanup_error)
                actual_signals = [event[1] for event in events if event[0] == "signal"]
                self.assertEqual(actual_signals, expected_signals)
                expected_events = [("initial-probe", None)]
                if initial_group_exists:
                    expected_events.append(("signal", signal.SIGTERM))
                    expected_events.append(("post-term-probe", None))
                if group_exists:
                    expected_events.append(("signal", kill_signal))
                expected_events.extend(
                    [
                        ("status", None),
                    ]
                )
                if group_exists:
                    expected_events.append(("post-kill-probe", None))
                expected_events.append(("wait", None))
                self.assertEqual(events, expected_events)
                process.poll.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_group_cleanup_invalidates_residual_or_unprobeable_group_after_kill(
        self,
    ) -> None:
        for final_probe, expected_detail in (
            ((True, None), "bound process group remained after final SIGKILL"),
            (
                (True, "injected final probe failure"),
                "failed to prove bound process-group absence after final SIGKILL",
            ),
        ):
            with self.subTest(final_probe=final_probe):
                process = mock.Mock()
                process.pid = 12345
                process.wait.return_value = -signal.SIGKILL
                selector = mock.Mock()
                with mock.patch.object(
                    project_journal,
                    "_bound_process_group_exists",
                    return_value=(True, None),
                ):
                    with mock.patch.object(
                        project_journal,
                        "_signal_process_group",
                        return_value=project_journal._ProcessSignalResult(
                            target_existed=True
                        ),
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_discard_selector_output",
                        ):
                            with mock.patch.object(
                                project_journal,
                                "_wait_for_bound_process_group_absence",
                                side_effect=[(True, None), final_probe],
                            ) as wait_for_absence:
                                with mock.patch.object(
                                    project_journal,
                                    "_wait_for_process_status_without_reaping",
                                    return_value=-signal.SIGKILL,
                                ):
                                    with mock.patch.object(
                                        project_journal,
                                        "_close_selector",
                                    ):
                                        cleanup_error = project_journal._terminate_process_group_and_reap(
                                            process,
                                            selector,
                                        )

                self.assertIsNotNone(cleanup_error)
                self.assertIn(expected_detail, cleanup_error)
                self.assertEqual(wait_for_absence.call_count, 2)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_process_group_cleanup_reports_final_reap_failure(self) -> None:
        process = mock.Mock()
        process.pid = 12345
        process.wait.side_effect = subprocess.TimeoutExpired("test", 1)
        selector = mock.Mock()

        with mock.patch.object(
            project_journal,
            "_signal_process_group",
            return_value=project_journal._ProcessSignalResult(target_existed=True),
        ):
            with mock.patch.object(project_journal, "_discard_selector_output"):
                with mock.patch.object(
                    project_journal,
                    "_wait_for_bound_process_group_absence",
                    return_value=(False, None),
                ):
                    with mock.patch.object(project_journal, "_close_selector"):
                        with mock.patch.object(
                            project_journal,
                            "_wait_for_process_status_without_reaping",
                            return_value=-signal.SIGTERM,
                        ):
                            cleanup_error = (
                                project_journal._terminate_process_group_and_reap(
                                    process,
                                    selector,
                                )
                            )

        self.assertIn(
            "direct child did not exit before the cleanup deadline",
            cleanup_error,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_final_reap_interrupt_does_not_resignal_released_group(self) -> None:
        original_reap = project_journal._reap_after_final_group_signal

        def reap_then_interrupt(
            process: subprocess.Popen[bytes],
            expected_returncode: int | None,
            deadline: float,
        ) -> str | None:
            self.assertIsNone(original_reap(process, expected_returncode, deadline))
            raise KeyboardInterrupt

        with mock.patch.object(
            project_journal,
            "_reap_after_final_group_signal",
            side_effect=reap_then_interrupt,
        ):
            with mock.patch.object(
                project_journal,
                "_bound_process_group_exists",
                return_value=(False, None),
            ):
                with mock.patch.object(
                    project_journal,
                    "_signal_process_group",
                ) as signal_group:
                    with self.assertRaises(KeyboardInterrupt):
                        self.capture_process(
                            [sys.executable, "-c", "pass"],
                            timeout_seconds=5,
                            stdout_limit=1024,
                        )

        signal_group.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_final_reap_interrupt_reports_cleanup_incomplete(self) -> None:
        original_reap = project_journal._reap_after_final_group_signal

        def reap_then_interrupt(
            process: subprocess.Popen[bytes],
            expected_returncode: int | None,
            deadline: float,
        ) -> str | None:
            self.assertIsNone(original_reap(process, expected_returncode, deadline))
            raise KeyboardInterrupt

        with mock.patch.object(
            project_journal,
            "_reap_after_final_group_signal",
            side_effect=reap_then_interrupt,
        ):
            with mock.patch.object(
                project_journal,
                "_bound_process_group_exists",
                return_value=(False, None),
            ):
                with self.assertRaises(KeyboardInterrupt) as raised:
                    self.capture_process(
                        [sys.executable, "-c", "pass"],
                        timeout_seconds=5,
                        stdout_limit=1024,
                    )

        detail = "\n".join(
            [
                *getattr(raised.exception, "__notes__", ()),
                *(str(value) for value in raised.exception.args),
            ]
        )
        self.assertIn(
            "cleanup-incomplete: final direct-child reap was interrupted",
            detail,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_cleanup_interrupt_reports_without_retrying_numeric_group(self) -> None:
        spawned: list[subprocess.Popen[bytes]] = []
        original_popen = subprocess.Popen

        def capture_popen(
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            process = original_popen(*args, **kwargs)
            spawned.append(process)
            return process

        parser = project_journal._IndexStageStreamParser()
        try:
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=capture_popen,
            ):
                with mock.patch.object(
                    project_journal,
                    "_terminate_process_group_and_reap",
                    side_effect=KeyboardInterrupt,
                ) as cleanup:
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        self.capture_process(
                            [
                                sys.executable,
                                "-c",
                                "import os; os.write(1, b'invalid')",
                            ],
                            timeout_seconds=5,
                            stdout_limit=1024,
                            stdout_feed=parser.feed,
                            stdout_finish=parser.finish,
                        )
        finally:
            for process in spawned:
                process.wait(timeout=5)

        cleanup.assert_called_once()
        detail = "\n".join(
            [
                *getattr(raised.exception, "__notes__", ()),
                *(str(value) for value in raised.exception.args),
            ]
        )
        self.assertIn(
            "cleanup-incomplete: process-group cleanup was interrupted",
            detail,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_async_interrupt_at_cleanup_handoff_never_signals_or_retries(
        self,
    ) -> None:
        spawned: list[subprocess.Popen[bytes]] = []
        original_popen = subprocess.Popen
        original_claim = project_journal._ProcessOwnership.claim_group_cleanup
        interrupted = False

        def capture_popen(
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            process = original_popen(*args, **kwargs)
            spawned.append(process)
            return process

        def claim_then_interrupt(
            ownership: project_journal._ProcessOwnership,
        ) -> None:
            nonlocal interrupted
            original_claim(ownership)
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt

        parser = project_journal._IndexStageStreamParser()
        try:
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=capture_popen,
            ):
                with mock.patch.object(
                    project_journal._ProcessOwnership,
                    "claim_group_cleanup",
                    side_effect=claim_then_interrupt,
                    autospec=True,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_terminate_process_group_and_reap",
                    ) as cleanup:
                        with mock.patch.object(
                            project_journal,
                            "_signal_process_group",
                        ) as signal_group:
                            with self.assertRaises(KeyboardInterrupt) as raised:
                                self.capture_process(
                                    [
                                        sys.executable,
                                        "-c",
                                        "import os; os.write(1, b'invalid')",
                                    ],
                                    timeout_seconds=5,
                                    stdout_limit=1024,
                                    stdout_feed=parser.feed,
                                    stdout_finish=parser.finish,
                                )
        finally:
            for process in spawned:
                process.wait(timeout=5)

        cleanup.assert_not_called()
        signal_group.assert_not_called()
        detail = "\n".join(
            [
                *getattr(raised.exception, "__notes__", ()),
                *(str(value) for value in raised.exception.args),
            ]
        )
        self.assertIn(
            "cleanup-incomplete: process-group cleanup was interrupted",
            detail,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_async_interrupt_after_reap_handoff_reaps_without_resignal(
        self,
    ) -> None:
        spawned: list[subprocess.Popen[bytes]] = []
        original_popen = subprocess.Popen
        original_transfer = project_journal._ProcessOwnership.transfer_to_reap
        interrupted = False

        def capture_popen(
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            process = original_popen(*args, **kwargs)
            spawned.append(process)
            return process

        def transfer_then_interrupt(
            ownership: project_journal._ProcessOwnership,
            expected_returncode: int | None,
        ) -> None:
            nonlocal interrupted
            original_transfer(ownership, expected_returncode)
            if not interrupted:
                interrupted = True
                raise KeyboardInterrupt

        with mock.patch.object(
            project_journal.subprocess,
            "Popen",
            side_effect=capture_popen,
        ):
            with mock.patch.object(
                project_journal._ProcessOwnership,
                "transfer_to_reap",
                side_effect=transfer_then_interrupt,
                autospec=True,
            ):
                with mock.patch.object(
                    project_journal,
                    "_signal_process_group",
                ) as signal_group:
                    with self.assertRaises(KeyboardInterrupt) as raised:
                        self.capture_process(
                            [sys.executable, "-c", "pass"],
                            timeout_seconds=5,
                            stdout_limit=1024,
                        )

        signal_group.assert_not_called()
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].returncode)
        detail = "\n".join(
            [
                *getattr(raised.exception, "__notes__", ()),
                *(str(value) for value in raised.exception.args),
            ]
        )
        self.assertIn(
            "cleanup-incomplete: final direct-child reap was interrupted",
            detail,
        )

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_identity_loss_skips_numeric_process_group_cleanup(self) -> None:
        spawned: list[subprocess.Popen[bytes]] = []
        original_popen = subprocess.Popen

        def capture_popen(
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            process = original_popen(*args, **kwargs)
            spawned.append(process)
            return process

        try:
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=capture_popen,
            ):
                with mock.patch.object(
                    project_journal,
                    "_wait_for_process_status_without_reaping",
                    side_effect=project_journal._ProcessIdentityLost(
                        "simulated leader identity loss"
                    ),
                ):
                    with mock.patch.object(
                        project_journal,
                        "_terminate_process_group_and_reap",
                    ) as cleanup:
                        with mock.patch.object(
                            project_journal,
                            "_signal_process_group",
                        ) as signal_group:
                            with self.assertRaisesRegex(
                                project_journal.UserError,
                                "cleanup was skipped after leader identity loss",
                            ):
                                self.capture_process(
                                    [sys.executable, "-c", "pass"],
                                    timeout_seconds=5,
                                    stdout_limit=1024,
                                )
        finally:
            for process in spawned:
                process.wait(timeout=5)

        cleanup.assert_not_called()
        signal_group.assert_not_called()

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_identity_loss_during_cleanup_never_retries_numeric_group_probe(
        self,
    ) -> None:
        spawned: list[subprocess.Popen[bytes]] = []
        original_popen = subprocess.Popen

        def capture_popen(
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            process = original_popen(*args, **kwargs)
            spawned.append(process)
            return process

        with mock.patch.object(
            project_journal.subprocess,
            "Popen",
            side_effect=capture_popen,
        ):
            with mock.patch.object(
                project_journal,
                "_bound_process_group_exists",
                side_effect=project_journal._ProcessIdentityLost(
                    "simulated SIGCHLD waitability loss during cleanup"
                ),
            ) as group_probe:
                with mock.patch.object(
                    project_journal,
                    "_signal_process_group",
                ) as signal_group:
                    with self.assertRaisesRegex(
                        project_journal.UserError,
                        "cleanup was skipped after leader identity loss",
                    ):
                        self.capture_process(
                            [sys.executable, "-c", "pass"],
                            timeout_seconds=5,
                            stdout_limit=1024,
                        )

        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].returncode)
        group_probe.assert_called_once()
        signal_group.assert_not_called()

    def test_bounded_capture_surfaces_cleanup_incomplete(self) -> None:
        parser = project_journal._IndexStageStreamParser()
        original_cleanup = project_journal._terminate_process_group_and_reap

        def cleanup_then_report(
            process: subprocess.Popen[bytes],
            selector: object,
            ownership: project_journal._ProcessOwnership,
            known_returncode: int | None = None,
        ) -> str:
            actual_error = original_cleanup(
                process,
                selector,
                ownership,
                known_returncode=known_returncode,
            )
            details = [
                detail
                for detail in (
                    actual_error,
                    "simulated unreaped process group",
                )
                if detail
            ]
            return "; ".join(details)

        with mock.patch.object(
            project_journal,
            "_terminate_process_group_and_reap",
            side_effect=cleanup_then_report,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "cleanup-incomplete after exit 0: .*simulated unreaped process group",
            ):
                self.capture_process(
                    [sys.executable, "-c", "import os; os.write(1, b'incomplete')"],
                    timeout_seconds=5,
                    stdout_limit=1024,
                    stdout_feed=parser.feed,
                    stdout_finish=parser.finish,
                )

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_process_group_cleanup_failure_preserves_original_error(self) -> None:
        original_cleanup = project_journal._terminate_process_group_and_reap
        original_error = LegacyUnsupportedPlatform(
            "original bounded-process action failure",
        )
        original_args = original_error.args
        observed_ownership: list[project_journal._ProcessOwnership] = []

        def reject_output(_chunk: bytes) -> None:
            raise original_error

        def cleanup_then_report(
            process: subprocess.Popen[bytes],
            selector: object,
            ownership: project_journal._ProcessOwnership,
            known_returncode: int | None = None,
        ) -> str:
            observed_ownership.append(ownership)
            self.assertEqual(ownership.state, "cleanup-claimed")
            actual_error = original_cleanup(
                process,
                selector,
                ownership,
                known_returncode=known_returncode,
            )
            details = [
                detail
                for detail in (
                    actual_error,
                    "simulated process-group cleanup failure",
                )
                if detail
            ]
            return "; ".join(details)

        with mock.patch.object(
            project_journal,
            "_terminate_process_group_and_reap",
            side_effect=cleanup_then_report,
        ):
            try:
                self.capture_process(
                    [sys.executable, "-c", "import os; os.write(1, b'x')"],
                    timeout_seconds=5,
                    stdout_limit=1024,
                    stdout_feed=reject_output,
                )
            except LegacyUnsupportedPlatform as exc:
                raised_error = exc
            else:
                self.fail("expected original bounded-process action failure")

        self.assertIs(raised_error, original_error)
        self.assertIs(type(raised_error), LegacyUnsupportedPlatform)
        self.assertEqual(
            raised_error.code,
            project_journal.UnsupportedPlatform.code,
        )
        self.assertEqual(raised_error.args, original_args)
        self.assertEqual(len(observed_ownership), 1)
        self.assertEqual(observed_ownership[0].state, "cleanup-incomplete")
        notes = "\n".join(getattr(raised_error, "__notes__", ()))
        self.assertIn("cleanup-incomplete", notes)
        self.assertIn("simulated process-group cleanup failure", notes)
        traceback_names: list[str] = []
        traceback = raised_error.__traceback__
        while traceback is not None:
            traceback_names.append(traceback.tb_frame.f_code.co_name)
            traceback = traceback.tb_next
        self.assertIn("reject_output", traceback_names)
        self.assertNotIn("_add_exception_detail", traceback_names)

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_error_cleanup_exception_preserves_original_across_ownership_states(
        self,
    ) -> None:
        actual_cleanup = project_journal._terminate_process_group_and_reap

        for mode, cleanup_failure, expected_state in (
            (
                "cleanup-claimed",
                RuntimeError("simulated claimed cleanup exception"),
                "cleanup-incomplete",
            ),
            (
                "reap-only",
                LegacyInterrupt("simulated reap handoff interruption"),
                "released",
            ),
            (
                "released",
                RuntimeError("simulated post-cleanup exception"),
                "released",
            ),
        ):
            with self.subTest(mode=mode):
                original_error = LegacyUnsupportedPlatform(
                    f"original action failure before {mode}",
                )
                original_args = original_error.args
                observed_ownership: list[project_journal._ProcessOwnership] = []
                observed_processes: list[subprocess.Popen[bytes]] = []

                def reject_output(_chunk: bytes) -> None:
                    raise original_error

                def cleanup_then_raise(
                    process: subprocess.Popen[bytes],
                    selector: object,
                    ownership: project_journal._ProcessOwnership,
                    known_returncode: int | None = None,
                ) -> str | None:
                    observed_ownership.append(ownership)
                    observed_processes.append(process)
                    self.assertEqual(ownership.state, "cleanup-claimed")
                    self.assertIsNone(known_returncode)
                    if mode == "cleanup-claimed":
                        process.terminate()
                        process.wait(timeout=5)
                    elif mode == "reap-only":
                        process.terminate()
                        expected_returncode = (
                            project_journal._wait_for_process_status_without_reaping(
                                process,
                                time.monotonic() + 5,
                                "test child did not reach terminal state",
                                interruptible=False,
                            )
                        )
                        ownership.transfer_to_reap(expected_returncode)
                    else:
                        self.assertIsNone(
                            actual_cleanup(
                                process,
                                selector,
                                ownership,
                                known_returncode=known_returncode,
                            )
                        )
                        self.assertEqual(ownership.state, "released")
                    raise cleanup_failure

                with mock.patch.object(
                    project_journal,
                    "_terminate_process_group_and_reap",
                    side_effect=cleanup_then_raise,
                ):
                    try:
                        self.capture_process(
                            [
                                sys.executable,
                                "-c",
                                "import os, time; os.write(1, b'x'); time.sleep(30)",
                            ],
                            timeout_seconds=5,
                            stdout_limit=1024,
                            stdout_feed=reject_output,
                        )
                    except LegacyUnsupportedPlatform as exc:
                        raised_error = exc
                    else:
                        self.fail("expected original bounded-process action failure")

                self.assertIs(raised_error, original_error)
                self.assertIs(type(raised_error), LegacyUnsupportedPlatform)
                self.assertEqual(
                    raised_error.code,
                    project_journal.UnsupportedPlatform.code,
                )
                self.assertEqual(raised_error.args, original_args)
                self.assertEqual(len(observed_ownership), 1)
                self.assertEqual(observed_ownership[0].state, expected_state)
                self.assertEqual(len(observed_processes), 1)
                self.assertIsNotNone(observed_processes[0].returncode)
                notes = "\n".join(getattr(raised_error, "__notes__", ()))
                self.assertIn(str(cleanup_failure), notes)
                if expected_state == "cleanup-incomplete":
                    self.assertIn(
                        "interrupted before the reap-only handoff",
                        notes,
                    )
                traceback_names: list[str] = []
                traceback = raised_error.__traceback__
                while traceback is not None:
                    traceback_names.append(traceback.tb_frame.f_code.co_name)
                    traceback = traceback.tb_next
                self.assertIn("reject_output", traceback_names)
                self.assertNotIn("cleanup_then_raise", traceback_names)

    def test_bounded_capture_kills_process_group_on_stdout_overflow(
        self,
    ) -> None:
        marker = self.root / "overflow-child-survived"
        ready = self.root / "overflow-child-started"
        child = self.root / "delayed-marker.py"
        child.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import sys
                import time

                time.sleep(0.5)
                pathlib.Path(sys.argv[1]).write_text("survived", encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        producer = self.root / "overflow-producer.py"
        producer.write_text(
            textwrap.dedent(
                """\
                import os
                import pathlib
                import subprocess
                import sys
                import time

                subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
                pathlib.Path(sys.argv[3]).write_text("started", encoding="utf-8")
                os.write(1, b"x" * 8192)
                time.sleep(30)
                """
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            project_journal.UserError,
            "test stdout exceeds limit",
        ):
            self.capture_process(
                [
                    sys.executable,
                    str(producer),
                    str(child),
                    str(marker),
                    str(ready),
                ],
                timeout_seconds=5,
                stdout_limit=1024,
            )

        self.assertTrue(ready.exists())
        time.sleep(0.7)
        self.assertFalse(marker.exists())

    def test_bounded_capture_kills_process_group_on_stream_parse_error(
        self,
    ) -> None:
        marker = self.root / "parse-child-survived"
        ready = self.root / "parse-child-started"
        child = self.root / "parse-delayed-marker.py"
        child.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import sys
                import time

                time.sleep(0.5)
                pathlib.Path(sys.argv[1]).write_text("survived", encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        producer = self.root / "parse-producer.py"
        producer.write_text(
            textwrap.dedent(
                """\
                import os
                import pathlib
                import subprocess
                import sys
                import time

                subprocess.Popen([sys.executable, sys.argv[1], sys.argv[2]])
                pathlib.Path(sys.argv[3]).write_text("started", encoding="utf-8")
                os.write(1, b"malformed-stage-record\\0")
                time.sleep(30)
                """
            ),
            encoding="utf-8",
        )
        parser = project_journal._IndexStageStreamParser()

        with self.assertRaisesRegex(
            project_journal.UserError,
            "malformed git ls-files stage record",
        ):
            self.capture_process(
                [
                    sys.executable,
                    str(producer),
                    str(child),
                    str(marker),
                    str(ready),
                ],
                timeout_seconds=5,
                stdout_limit=1024,
                stdout_feed=parser.feed,
                stdout_finish=parser.finish,
            )

        self.assertTrue(ready.exists())
        time.sleep(0.7)
        self.assertFalse(marker.exists())

    def test_bounded_capture_kills_process_group_on_timeout(self) -> None:
        marker = self.root / "timeout-child-survived"
        ready = self.root / "timeout-child-started"
        release = self.root / "timeout-child-release"
        child = self.root / "timeout-delayed-marker.py"
        child.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import sys
                import time

                release = pathlib.Path(sys.argv[2])
                while not release.exists():
                    time.sleep(0.005)
                time.sleep(0.3)
                pathlib.Path(sys.argv[1]).write_text("survived", encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        producer = self.root / "timeout-producer.py"
        producer.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import subprocess
                import sys
                import time

                subprocess.Popen(
                    [sys.executable, sys.argv[1], sys.argv[2], sys.argv[4]]
                )
                pathlib.Path(sys.argv[3]).write_text("started", encoding="utf-8")
                time.sleep(30)
                """
            ),
            encoding="utf-8",
        )

        argv = [
            sys.executable,
            str(producer),
            str(child),
            str(marker),
            str(ready),
            str(release),
        ]
        process = subprocess.Popen(
            argv,
            env={"PATH": os.environ.get("PATH", "")},
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        def cleanup_process() -> None:
            if process.returncode is not None:
                return
            project_journal._signal_process_group(
                process,
                getattr(
                    project_journal.signal,
                    "SIGKILL",
                    project_journal.signal.SIGTERM,
                ),
            )
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()

        self.addCleanup(cleanup_process)
        ready_deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < ready_deadline:
            self.assertIsNone(process.poll())
            time.sleep(0.01)
        self.assertTrue(ready.exists())
        release.write_text("go", encoding="utf-8")

        with mock.patch.object(
            project_journal.subprocess,
            "Popen",
            return_value=process,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "test process timed out",
            ):
                self.capture_process(
                    argv,
                    timeout_seconds=0.1,
                    stdout_limit=1024,
                )

        time.sleep(0.4)
        self.assertFalse(marker.exists())

    def test_cat_file_batch_timeout_kills_descendant_process_group(self) -> None:
        repo = self.init_repo()
        marker = self.root / "batch-timeout-child-survived"
        ready = self.root / "batch-timeout-child-started"
        release = self.root / "batch-timeout-child-release"
        child = self.root / "batch-timeout-delayed-marker.py"
        child.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import sys
                import time

                release = pathlib.Path(sys.argv[2])
                while not release.exists():
                    time.sleep(0.005)
                time.sleep(0.3)
                pathlib.Path(sys.argv[1]).write_text("survived", encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        producer = self.root / "batch-timeout-producer.py"
        producer.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import subprocess
                import sys
                import time

                subprocess.Popen(
                    [sys.executable, sys.argv[1], sys.argv[2], sys.argv[4]]
                )
                pathlib.Path(sys.argv[3]).write_text("started", encoding="utf-8")
                time.sleep(30)
                """
            ),
            encoding="utf-8",
        )
        argv = [
            sys.executable,
            str(producer),
            str(child),
            str(marker),
            str(ready),
            str(release),
        ]
        process = subprocess.Popen(
            argv,
            env={"PATH": os.environ.get("PATH", "")},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        def cleanup_process() -> None:
            if process.returncode is not None:
                return
            project_journal._signal_process_group(
                process,
                getattr(
                    project_journal.signal,
                    "SIGKILL",
                    project_journal.signal.SIGTERM,
                ),
            )
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()

        self.addCleanup(cleanup_process)
        ready_deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < ready_deadline:
            self.assertIsNone(process.poll())
            time.sleep(0.01)
        self.assertTrue(ready.exists())
        release.write_text("go", encoding="utf-8")
        blob = project_journal.IndexJournalBlob(
            mode=b"100644",
            oid="a" * 40,
            raw_path=b"docs/project_journal/entry.md",
            rel_path="docs/project_journal/entry.md",
        )

        with mock.patch.object(
            project_journal.subprocess,
            "Popen",
            return_value=process,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "git cat-file batch timed out",
            ):
                project_journal._read_index_blobs_batch(
                    repo,
                    [blob],
                    timeout_seconds=0.1,
                )

        time.sleep(0.4)
        self.assertFalse(marker.exists())

    def test_cat_file_batch_nonzero_exit_kills_detached_stream_descendant(
        self,
    ) -> None:
        repo = self.init_repo()
        marker = self.root / "batch-nonzero-child-survived"
        ready = self.root / "batch-nonzero-child-started"
        release = self.root / "batch-nonzero-child-release"
        child = self.root / "batch-nonzero-delayed-marker.py"
        child.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import sys
                import time

                release = pathlib.Path(sys.argv[2])
                while not release.exists():
                    time.sleep(0.005)
                time.sleep(0.3)
                pathlib.Path(sys.argv[1]).write_text("survived", encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        producer = self.root / "batch-nonzero-producer.py"
        producer.write_text(
            textwrap.dedent(
                """\
                import os
                import pathlib
                import subprocess
                import sys

                subprocess.Popen(
                    [sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                pathlib.Path(sys.argv[4]).write_text("started", encoding="utf-8")
                sys.stdin.buffer.readline()
                oid = sys.argv[5].encode("ascii")
                os.write(1, oid + b" blob 1\\nx\\n")
                raise SystemExit(2)
                """
            ),
            encoding="utf-8",
        )
        oid = "a" * 40
        argv = [
            sys.executable,
            str(producer),
            str(child),
            str(marker),
            str(release),
            str(ready),
            oid,
        ]
        process = subprocess.Popen(
            argv,
            env={"PATH": os.environ.get("PATH", "")},
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )

        def cleanup_process() -> None:
            if process.returncode is not None:
                return
            project_journal._signal_process_group(
                process,
                getattr(
                    project_journal.signal,
                    "SIGKILL",
                    project_journal.signal.SIGTERM,
                ),
            )
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()

        self.addCleanup(cleanup_process)
        blob = project_journal.IndexJournalBlob(
            mode=b"100644",
            oid=oid,
            raw_path=b"docs/project_journal/entry.md",
            rel_path="docs/project_journal/entry.md",
        )

        with mock.patch.object(
            project_journal.subprocess,
            "Popen",
            return_value=process,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                (
                    "failed to read tracked journal index blobs"
                    "|cleanup-incomplete after exit 2"
                ),
            ):
                project_journal._read_index_blobs_batch(
                    repo,
                    [blob],
                    timeout_seconds=5,
                )

        self.assertTrue(ready.exists())
        release.write_text("go", encoding="utf-8")
        time.sleep(0.4)
        self.assertFalse(marker.exists())

    @unittest.skipUnless(os.name == "posix", "POSIX process-group contract")
    def test_bounded_capture_zero_exit_cleans_detached_stream_descendant(
        self,
    ) -> None:
        marker = self.root / "zero-exit-child-survived"
        ready = self.root / "zero-exit-child-started"
        release = self.root / "zero-exit-child-release"
        child = self.root / "zero-exit-delayed-marker.py"
        child.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import sys
                import time

                release = pathlib.Path(sys.argv[2])
                while not release.exists():
                    time.sleep(0.005)
                time.sleep(0.3)
                pathlib.Path(sys.argv[1]).write_text("survived", encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )
        producer = self.root / "zero-exit-producer.py"
        producer.write_text(
            textwrap.dedent(
                """\
                import pathlib
                import subprocess
                import sys

                subprocess.Popen(
                    [sys.executable, sys.argv[1], sys.argv[2], sys.argv[3]],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                pathlib.Path(sys.argv[4]).write_text("started", encoding="utf-8")
                """
            ),
            encoding="utf-8",
        )

        result = self.capture_process(
            [
                sys.executable,
                str(producer),
                str(child),
                str(marker),
                str(release),
                str(ready),
            ],
            timeout_seconds=5,
            stdout_limit=1024,
        )

        self.assertEqual(result.returncode, 0)
        self.assertTrue(ready.exists())
        release.write_text("go", encoding="utf-8")
        time.sleep(0.4)
        self.assertFalse(marker.exists())

    def test_bounded_capture_enforces_stderr_limit(self) -> None:
        producer = self.root / "stderr-producer.py"
        producer.write_text(
            textwrap.dedent(
                """\
                import os
                import time

                os.write(2, b"x" * 2048)
                time.sleep(30)
                """
            ),
            encoding="utf-8",
        )

        with self.assertRaisesRegex(
            project_journal.UserError,
            "test stderr exceeds limit",
        ):
            self.capture_process(
                [sys.executable, str(producer)],
                timeout_seconds=5,
                stdout_limit=1024,
            )

    def test_validate_rejects_invalid_status_and_broken_superseded_link(self) -> None:
        repo = self.init_repo()
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-bad-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Bad Work",
            status="paused",
            updated="2026-05-05",
        )
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-04-old-d4e5f6.md",
            entry_id="20260504-d4e5f6",
            title="Old Work",
            status="superseded",
            updated="2026-05-04",
            superseded_by="missing-id",
        )

        result = self.run_cli("validate", "--repo", str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid status", result.stderr)
        self.assertIn("superseded_by target", result.stderr)

    def test_validate_rejects_empty_dates(self) -> None:
        repo = self.init_repo()
        path = repo / "docs/project_journal/2026/05/2026-05-05-empty-date-a1b2c3.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            textwrap.dedent(
                """\
                ---
                id: 20260505-a1b2c3
                title: Empty Date
                status: active
                created:
                updated:
                branch:
                pr:
                supersedes: []
                superseded_by:
                ---

                ## Summary
                """
            ),
            encoding="utf-8",
        )

        result = self.run_cli("validate", "--repo", str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("field 'created' must not be empty", result.stderr)
        self.assertIn("field 'updated' must not be empty", result.stderr)

    def test_skill_warns_to_use_bundled_helper_path(self) -> None:
        skill = SKILL_MD.read_text(encoding="utf-8")

        self.assertIn("### Helper Script Path", skill)
        self.assertIn(
            "<loaded-skill-dir>/scripts/project_journal.py",
            skill,
        )
        self.assertIn("<target-repo>/scripts/project_journal.py", skill)
        self.assertNotIn("Use `scripts/project_journal.py validate", skill)

    def test_skill_limits_auto_trigger_and_requires_first_adoption_need(self) -> None:
        skill = SKILL_MD.read_text(encoding="utf-8")
        frontmatter = skill.split("---", 2)[1]

        self.assertIn("Use only when repo policy requires this workflow", frontmatter)
        self.assertIn(
            "at least one valid tracked non-generated entry",
            frontmatter,
        )
        self.assertIn(
            "a task spans Codex sessions, a PR, or a durable workstream",
            frontmatter,
        )
        self.assertIn(
            "Require an explicit product need before introducing the first tracker",
            frontmatter,
        )
        self.assertIn(
            "treat the trigger as a reason to assess durable state, not as permission to create files",
            skill,
        )
        self.assertIn("adoption-status --repo <path>", skill)
        self.assertIn(
            "Directory presence, untracked files, and an empty or generated-`INDEX.md`-only directory do not establish adoption",
            skill,
        )
        self.assertIn(
            "accepts only unconflicted stage-0 regular-file entries and validates the exact indexed blob",
            skill,
        )
        self.assertIn("clears ambient Git control variables", skill)
        self.assertIn("bounded credential-free `git --version` gate", skill)
        self.assertIn("Git older than 2.45 fails closed", skill)
        self.assertIn(
            "remainder of the same five-second initialization deadline",
            skill,
        )
        self.assertIn(
            "classify a timeout, nonzero exit, or malformed response as non-cacheable",
            skill,
        )
        self.assertIn(
            "snapshot-creation source, destination, and directory descriptors",
            skill,
        )
        self.assertIn("one absolute monotonic deadline", skill)
        self.assertIn("frontmatter parsing, semantic validation", skill)
        self.assertIn("frontmatter field/list, validation-issue", skill)
        self.assertIn(
            "Keep each exact invalid path once for structured validity decisions",
            skill,
        )
        self.assertIn("stable JSON `path_ref`", skill)
        self.assertIn("final UTF-8-with-`backslashreplace` display bytes", skill)
        self.assertIn("failed `git ls-files` must never echo stdout", skill)
        self.assertIn("1 MiB aggregate retained-issue ceiling", skill)
        self.assertIn(
            "invalidates every member of a duplicate-ID group",
            skill,
        )
        self.assertIn(
            "index, entry, frontmatter field/list, validation-issue, byte, record, and stderr limits",
            skill,
        )
        self.assertIn("one bounded `git cat-file --batch` session", skill)
        self.assertIn("final raw index revalidation", skill)
        self.assertIn(
            "keep the direct child unreaped as the PID/PGID identity fence",
            skill,
        )
        self.assertIn("status is observed with `WNOWAIT`", skill)
        self.assertIn("Xcode Python 3.9.6", skill)
        self.assertIn("kqueue `NOTE_EXIT`", skill)
        self.assertIn("default waitable `SIGCHLD` semantics", skill)
        self.assertIn("reject `SA_NOCLDWAIT`", skill)
        self.assertIn("libc-injected `SA_RESTORER`", skill)
        self.assertIn(
            "before every numeric PID/PGID probe or signal",
            skill,
        )
        self.assertIn("Explicit ownership states", skill)
        self.assertIn("reuses the already claimed ownership object", skill)
        self.assertIn(
            "bounded cleanup exception evidence attaches to the original action exception",
            skill,
        )
        self.assertIn(
            "attach descriptor-close faults to the current parse, limit, replacement, or access-policy error",
            skill,
        )
        self.assertIn(
            "supports only macOS and Linux",
            skill,
        )
        self.assertIn("including other POSIX systems", skill)
        self.assertNotIn("other POSIX hosts retain signal-zero", skill)
        self.assertIn(
            "no numeric PGID is signalled after that fence is released",
            skill,
        )
        self.assertIn("reported as `cleanup-incomplete`", skill)
        self.assertIn(
            "`inconclusive` carries a structured `adoption_error` and null adoption fields",
            skill,
        )
        self.assertIn("system and global Git configuration", skill)
        self.assertIn("without following includes", skill)
        self.assertIn("explicit repo-local `core.hooksPath`", skill)
        self.assertIn(
            "distinguishes identity, content, and access-policy changes",
            skill,
        )
        self.assertIn(
            "verified descriptor bytes into a fresh owner-private command launch",
            skill,
        )
        self.assertIn(
            "Require every lexical directory or symlink marker, followed target, and descriptor-bound ancestor to be owned by root or the current user",
            skill,
        )
        self.assertIn(
            "reject every group/world-writable non-sticky directory",
            skill,
        )
        self.assertIn(
            "one lexical absolute endpoint without synchronous path canonicalization",
            skill,
        )
        self.assertIn(
            "immediately before and after `Popen`",
            skill,
        )
        self.assertIn(
            "A post-start mismatch enters the ordinary process-group/reap state machine",
            skill,
        )
        self.assertIn(
            "labels the original path as unverified",
            skill,
        )
        self.assertIn(
            "do not claim to stop a malicious same-UID replacement after the last pre-exec check",
            skill,
        )
        self.assertIn(
            "close-only ordinary failure becomes a `UserError`",
            skill,
        )
        self.assertIn(
            "repeats descriptor/path identity, access, size, and digest validation",
            skill,
        )
        self.assertIn(
            "an unverified terminal reports a locator only after path revalidation",
            skill,
        )
        self.assertIn(
            "preserve the exact original exception object, type, code, arguments, and traceback",
            skill,
        )
        self.assertIn(
            "source-snapshot descriptor inside that same preparation cleanup flow",
            skill,
        )
        self.assertIn("native no-replace or exchange rename semantics", skill)
        self.assertIn("reported recovery locator", skill)
        self.assertIn("installed-target-committed state", skill)
        self.assertIn(
            "completed cleanup and never claims a missing recovery object",
            skill,
        )
        self.assertIn("required `O_NOFOLLOW|O_NONBLOCK`", skill)
        self.assertIn("every ancestor identity/access policy", skill)
        self.assertIn(
            "preserve that exact action exception as primary",
            skill,
        )
        self.assertIn(
            "preserving the same object, type, arguments, and traceback",
            skill,
        )
        self.assertIn(
            "existing precedence for an earlier terminal-convergence failure "
            "or managed-signal propagation is unchanged",
            skill,
        )
        self.assertIn(
            "without `BaseException.add_note`",
            skill,
        )
        self.assertIn("clears a cached initialization failure", skill)
        self.assertIn("reserves its final bounded detail slot", skill)
        self.assertIn(
            "CLI parsing, including `--help`, completes before Git runtime initialization",
            skill,
        )
        self.assertIn(
            "held root-to-directory descriptor chain",
            skill,
        )
        self.assertIn("`path_unverified`", skill)
        self.assertIn(
            "allowing timestamp and directory child-entry churn",
            skill,
        )
        self.assertIn(
            "do not overwrite authoritative index adoption",
            skill,
        )
        self.assertIn("each root is enriched at most twice", skill)
        self.assertIn(
            "active `sessions` and flat `archived_sessions`",
            skill,
        )
        self.assertIn("`archived_sessions` as direct-child-only", skill)
        self.assertIn("131,072-association budget", skill)
        self.assertIn("`O_NOFOLLOW|O_NONBLOCK` descriptor", skill)
        self.assertIn("distinct `inspection_reason` values", skill)
        self.assertIn(
            "object identity (`st_dev`, `st_ino`), access policy",
            skill,
        )
        self.assertIn("append/truncation", skill)
        self.assertIn("Require `coverage_status: complete`", skill)
        self.assertIn(
            "full bounded `discovery_coverage` object, including a stable `coverage_id`, exactly once",
            skill,
        )
        self.assertIn("small `discovery_coverage_ref` to every partial row", skill)
        self.assertIn(
            "Buffer normalized distinct CWDs in first-seen order per rollout",
            skill,
        )
        self.assertIn(
            "Discard a failed rollout's pending buffer without rolling back aggregate counters",
            skill,
        )
        self.assertIn(
            "bind the retained lexical current path once with required `O_DIRECTORY|O_CLOEXEC|O_NONBLOCK`",
            skill,
        )
        self.assertIn(
            "inspect `.git` only with descriptor-relative no-follow `stat`",
            skill,
        )
        self.assertIn(
            "label any lexical marker hint `path_unverified`",
            skill,
        )
        self.assertIn(
            "Keep one descriptor owner authoritative",
            skill,
        )
        self.assertIn(
            "local current/parent variables are non-owning aliases",
            skill,
        )
        self.assertIn(
            "owner state distinguishes not-yet-closed from close-attempted descriptors",
            skill,
        )
        self.assertIn(
            "contextual error is not the identical ambient exception",
            skill,
        )
        self.assertIn("bounded three-line marker prefix", skill)
        self.assertIn(
            "Inaccessible journal, index, exclude, or hook paths remain null",
            skill,
        )
        self.assertIn(
            "Hook installation supports macOS and Linux only",
            skill,
        )
        self.assertIn(
            "`--includes --show-scope --show-origin --type=path --null --get core.hooksPath`",
            skill,
        )
        self.assertIn("`effective_hook_destination_changed`", skill)
        self.assertIn("`effective_hook_configuration_unverified`", skill)
        self.assertIn("point-in-time proof", skill)
        self.assertIn(
            "leave `docs/PROJECT_STATE.md`, `docs/PROJECT_TODO.md`, and `docs/project_journal/` unchanged",
            skill,
        )
        self.assertNotIn("Treat the docs as the default convention", skill)
        self.assertNotIn(
            "assume `docs/PROJECT_STATE.md` and `docs/PROJECT_TODO.md` should exist",
            skill,
        )
        self.assertNotIn(
            "If top-level trackers do not exist yet, create both files",
            skill,
        )
        self.assertNotIn("Keep both top-level docs", skill)
        self.assertIn(
            "When present, keep `docs/PROJECT_STATE.md` and `docs/PROJECT_TODO.md`",
            skill,
        )

    def test_skill_updates_smallest_layer_and_keeps_local_tools_conditional(
        self,
    ) -> None:
        skill = SKILL_MD.read_text(encoding="utf-8")

        self.assertIn(
            "update the smallest applicable existing or required layer without waiting for another prompt",
            skill,
        )
        self.assertIn(
            "Do not create both top-level files as a pair by default",
            skill,
        )
        self.assertIn(
            "only when the active workflow needs multi-entry navigation",
            skill,
        )
        self.assertIn(
            "only when the user explicitly wants opt-in local hook refresh",
            skill,
        )
        self.assertIn(
            "In squash-merge repos, tracked journal docs should describe the target branch after the PR lands",
            skill,
        )

    def test_skill_metadata_and_references_preserve_adoption_gate(self) -> None:
        readme = README_MD.read_text(encoding="utf-8")
        openai_yaml = OPENAI_YAML.read_text(encoding="utf-8")
        templates = TEMPLATES_MD.read_text(encoding="utf-8")
        migration = MIGRATION_PLAYBOOK_MD.read_text(encoding="utf-8")

        self.assertIn("$project-journal", openai_yaml)
        self.assertIn("smallest applicable journal layer", openai_yaml)
        self.assertIn("repo policy requires journaling", openai_yaml)
        self.assertIn(
            "valid tracked non-generated journal entry proves adoption",
            openai_yaml,
        )
        self.assertIn("task has an explicit durable-state need", openai_yaml)
        self.assertIn("supported macOS or Linux hosts", openai_yaml)
        self.assertIn(
            "partial active/archive discovery coverage as inconclusive",
            openai_yaml,
        )
        self.assertNotIn("ignored local journal index", openai_yaml)
        self.assertIn(
            "valid tracked non-generated journal entry",
            readme,
        )
        self.assertIn(
            "generated `INDEX.md` does not establish adoption",
            readme,
        )
        self.assertIn("ignores ambient Git control/configuration redirections", readme)
        self.assertIn(
            "requires those exact bytes to return a bounded credential-free",
            readme,
        )
        self.assertIn(
            "remainder of the same five-second initialization deadline",
            readme,
        )
        self.assertIn(
            "timeout, nonzero exit, or malformed response is a non-cacheable",
            readme,
        )
        self.assertIn(
            "source, destination, and directory descriptors through one bounded precedence helper",
            readme,
        )
        self.assertIn(
            "verified descriptor bytes into a fresh owner-private command launch",
            readme,
        )
        self.assertIn(
            "Every lexical directory or symlink marker, followed target, and descriptor-bound ancestor must be owned by root or the current user",
            readme,
        )
        self.assertIn(
            "group/world-writable non-sticky directories and extended ACLs are rejected",
            readme,
        )
        self.assertIn(
            "binds one lexical absolute temporary-root endpoint without synchronous path canonicalization",
            readme,
        )
        self.assertIn(
            "immediately before and after `Popen`",
            readme,
        )
        self.assertIn(
            "A post-start mismatch enters process-group/reap cleanup",
            readme,
        )
        self.assertIn(
            "unverified original path hint rather than a false locator",
            readme,
        )
        self.assertIn(
            "do not claim to prevent a malicious same-UID replacement after the final pre-exec check",
            readme,
        )
        self.assertIn(
            "close-only ordinary failure is wrapped as `UserError`",
            readme,
        )
        self.assertIn(
            "locks the launch directory against ordinary replacement",
            readme,
        )
        self.assertIn(
            "reports a locator only after path revalidation",
            readme,
        )
        self.assertIn("byte, record, and stderr bounds", readme)
        self.assertIn("one bounded `git cat-file --batch` session", readme)
        self.assertIn("one absolute monotonic deadline", readme)
        self.assertIn("structured per-path validity", readme)
        self.assertIn("entry/field/list/issue budgets", readme)
        self.assertIn(
            "unreaped direct child as the PID/PGID identity fence",
            readme,
        )
        self.assertIn("Xcode Python 3.9.6", readme)
        self.assertIn("kqueue `NOTE_EXIT`", readme)
        self.assertIn("default waitable `SIGCHLD` semantics", readme)
        self.assertIn("rejects `SA_NOCLDWAIT`", readme)
        self.assertIn("libc-injected `SA_RESTORER`", readme)
        self.assertIn(
            "before every numeric PID/PGID probe or signal",
            readme,
        )
        self.assertIn("Explicit cleanup ownership states", readme)
        self.assertIn("never signals a numeric PGID after the final reap", readme)
        self.assertIn("reports `cleanup-incomplete`", readme)
        self.assertIn("system and global Git configuration", readme)
        self.assertIn("native no-replace or exchange rename semantics", readme)
        self.assertIn("installed-target-committed state", readme)
        self.assertIn(
            "completed cleanup and never claims a missing recovery object",
            readme,
        )
        self.assertIn(
            "preserving the same object, type, arguments, and traceback",
            readme,
        )
        self.assertIn(
            "source-snapshot descriptor closes inside that same preparation cleanup flow",
            readme,
        )
        self.assertIn(
            "existing precedence for an earlier terminal-convergence failure "
            "or managed-signal propagation is unchanged",
            readme,
        )
        self.assertIn("bind the complete absolute path", readme)
        self.assertIn("structured `discovery_error`", readme)
        self.assertIn("supports only macOS and Linux", readme)
        self.assertIn("including other POSIX systems", readme)
        self.assertNotIn("other POSIX hosts retain signal-zero", readme)
        self.assertIn("each root is enriched at most twice", readme)
        self.assertIn(
            "active `sessions` and flat `archived_sessions`",
            readme,
        )
        self.assertIn(
            "archive source accepts only direct `rollout-*.jsonl` children",
            readme,
        )
        self.assertIn(
            "131,072 retained logical rollout associations",
            readme,
        )
        self.assertIn("one shared 60-second monotonic deadline", readme)
        self.assertIn("required `O_NOFOLLOW|O_NONBLOCK`", readme)
        self.assertIn("distinct `inspection_reason`", readme)
        self.assertIn(
            "object identity (`st_dev`, `st_ino`), access policy",
            readme,
        )
        self.assertIn("append/truncation", readme)
        self.assertIn("`coverage_status: complete`", readme)
        self.assertIn("one deterministic final-sort anchor", readme)
        self.assertIn("small `discovery_coverage_ref`", readme)
        self.assertIn(
            "first-seen order in a per-rollout buffer",
            readme,
        )
        self.assertIn(
            "failed rollout discards its pending buffer without rolling back aggregate byte, record, verification-byte, or distinct-CWD counters",
            readme,
        )
        self.assertIn(
            "descriptor-bound scan over at most `MAX_DISCOVERY_CWD_COMPONENTS + 1` existing ancestors",
            readme,
        )
        self.assertIn(
            'Every `.git` classification then uses only no-follow `stat(".git", dir_fd=current_fd)`',
            readme,
        )
        self.assertIn(
            "explicitly `path_unverified` lexical hint",
            readme,
        )
        self.assertIn(
            "One descriptor owner is authoritative",
            readme,
        )
        self.assertIn(
            "Context exit retries an interrupted drain",
            readme,
        )
        self.assertIn("first uncertain close failure", readme)
        self.assertIn("ambient exception recorded at close start", readme)
        self.assertIn("three-line marker prefix", readme)
        self.assertIn(
            "Inaccessible journal, index, exclude, or hook paths",
            readme,
        )
        self.assertIn(
            "Opt-in hook installation supports macOS and Linux only",
            readme,
        )
        self.assertIn(
            "`--includes --show-scope --show-origin --type=path --null --get core.hooksPath`",
            readme,
        )
        self.assertIn("`effective_hook_destination_changed`", readme)
        self.assertIn("`effective_hook_configuration_unverified`", readme)
        self.assertIn("point-in-time proof", readme)
        self.assertIn("Every user-visible indexed-path diagnostic", readme)
        self.assertIn("failed `git ls-files` never echoes stdout", readme)
        self.assertIn("duplicate-ID group invalidation", readme)
        self.assertIn("without erasing authoritative index adoption", readme)
        self.assertIn("`adopted`, `unadopted`, or `inconclusive`", readme)
        self.assertIn(
            "explicit product need justifies first adoption",
            templates,
        )
        self.assertIn(
            "valid tracked non-generated journal entry establishes adoption",
            templates,
        )
        self.assertIn(
            "Do not create both top-level trackers",
            templates,
        )
        self.assertIn(
            "Treat discovery output and historical tracker presence as candidate evidence",
            migration,
        )
        self.assertIn("`discovery_coverage_ref.coverage_id`", migration)
        self.assertIn(
            "migration merely because a repository belongs to Joey",
            migration,
        )
        self.assertIn(
            "generated `INDEX.md` is not",
            migration,
        )

    def test_validate_rejects_broken_supersedes_link(self) -> None:
        repo = self.init_repo()
        path = (
            repo / "docs/project_journal/2026/05/2026-05-05-broken-supersedes-a1b2c3.md"
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            textwrap.dedent(
                """\
                ---
                id: 20260505-a1b2c3
                title: Broken Supersedes
                status: active
                created: 2026-05-05
                updated: 2026-05-05
                branch:
                pr:
                supersedes: [missing-id]
                superseded_by:
                ---

                ## Summary
                """
            ),
            encoding="utf-8",
        )

        result = self.run_cli("validate", "--repo", str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("supersedes target", result.stderr)

    def test_validate_rejects_broken_superseded_by_link_on_active_entry(self) -> None:
        repo = self.init_repo()
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-active-bad-link-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Active Bad Link",
            status="active",
            updated="2026-05-05",
            superseded_by="missing-id",
        )

        result = self.run_cli("validate", "--repo", str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("superseded_by target", result.stderr)

    def test_hook_commit_state_verified_phase_is_terminal(self) -> None:
        state = project_journal._HookCommitState()

        self.assertEqual(state.phase, "staged-cleanup-safe")
        self.assertFalse(state.installed_target_committed)
        state.mark_installed_target_committed("displaced-hook cleanup")
        self.assertTrue(state.installed_target_committed)
        self.assertEqual(state.pending_step, "displaced-hook cleanup")
        state.mark_temporary_consumed()
        self.assertEqual(state.pending_step, "directory durability")
        state.mark_directory_durable()
        self.assertEqual(state.pending_step, "final installed-target verification")
        state.mark_installed_target_verified()
        self.assertEqual(
            state.pending_step,
            "final effective hook destination verification",
        )

        state.mark_verified()

        self.assertEqual(state.phase, "verified")
        self.assertIsNone(state.pending_step)
        self.assertFalse(state.installed_target_committed)
        self.assertFalse(state.absent_rename_may_have_committed)
        self.assertFalse(state.must_preserve_temporary)
        state.mark_temporary_consumed()
        state.mark_directory_durable()
        state.mark_installed_target_verified()
        self.assertEqual(state.phase, "verified")
        self.assertIsNone(state.pending_step)

    def test_install_hooks_is_idempotent_and_hook_does_not_block(self) -> None:
        repo = self.init_repo()
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Alpha Work",
            status="active",
            updated="2026-05-05",
        )

        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        second = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(second.returncode, 0, second.stderr)

        for hook_name in project_journal.HOOK_NAMES:
            hook = repo / ".git/hooks" / hook_name
            self.assertTrue(hook.exists())
            content = hook.read_text(encoding="utf-8")
            self.assertIn(project_journal.HOOK_BEGIN, content)
            self.assertNotIn("project-journal-index.$$", content)

        hook_run = subprocess.run(
            [str(repo / ".git/hooks/post-merge")],
            cwd=repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(hook_run.returncode, 0, hook_run.stderr)
        self.assertTrue((repo / "docs/project_journal/INDEX.md").exists())
        self.assertFalse((repo / ".git/project-journal-index.log").exists())

        journal = repo / "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md"
        journal.write_text(
            journal.read_text(encoding="utf-8").replace(
                "status: active", "status: invalid"
            ),
            encoding="utf-8",
        )
        failing_hook_run = subprocess.run(
            [str(repo / ".git/hooks/post-merge")],
            cwd=repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(failing_hook_run.returncode, 0, failing_hook_run.stderr)
        log = repo / ".git/project-journal-index.log"
        self.assertTrue(log.exists())
        self.assertIn("invalid status", log.read_text(encoding="utf-8"))

    def test_installed_hook_ignores_ambient_git_repo_redirection(self) -> None:
        repo = self.init_repo("victim")
        journal = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-victim-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Victim Work",
            status="active",
            updated="2026-05-05",
        )
        attacker = self.init_repo("hook-attacker")
        attacker_journal = self.write_journal(
            attacker,
            "docs/project_journal/2026/05/2026-05-05-attacker-d4e5f6.md",
            entry_id="20260505-d4e5f6",
            title="Attacker Work",
            status="active",
            updated="2026-05-05",
        )
        attacker_add = run_git(
            attacker,
            "add",
            "--",
            str(attacker_journal.relative_to(attacker)),
        )
        self.assertEqual(attacker_add.returncode, 0, attacker_add.stderr)

        install = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(install.returncode, 0, install.stderr)
        hook = repo / ".git/hooks/post-merge"
        poison = {
            "PATH": os.environ.get("PATH", ""),
            "HOME": str(self.home),
            "TMPDIR": str(self.root),
            "GIT_ALTERNATE_OBJECT_DIRECTORIES": str(attacker / ".git/objects"),
            "GIT_COMMON_DIR": str(attacker / ".git"),
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "core.worktree",
            "GIT_CONFIG_VALUE_0": str(attacker),
            "GIT_DIR": str(attacker / ".git"),
            "GIT_INDEX_FILE": str(attacker / ".git/index"),
            "GIT_OBJECT_DIRECTORY": str(attacker / ".git/objects"),
            "GIT_WORK_TREE": str(attacker),
        }

        hook_run = subprocess.run(
            [str(hook)],
            cwd=repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=poison,
        )

        self.assertEqual(hook_run.returncode, 0, hook_run.stderr)
        self.assertTrue((repo / "docs/project_journal/INDEX.md").exists())
        self.assertFalse((attacker / "docs/project_journal/INDEX.md").exists())

        journal.write_text(
            journal.read_text(encoding="utf-8").replace(
                "status: active",
                "status: invalid",
            ),
            encoding="utf-8",
        )
        failing_hook_run = subprocess.run(
            [str(hook)],
            cwd=repo,
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=poison,
        )

        self.assertEqual(
            failing_hook_run.returncode,
            0,
            failing_hook_run.stderr,
        )
        victim_log = repo / ".git/project-journal-index.log"
        self.assertTrue(victim_log.exists())
        self.assertIn("invalid status", victim_log.read_text(encoding="utf-8"))
        self.assertFalse((attacker / ".git/project-journal-index.log").exists())

    def test_post_rewrite_hook_drains_stdin(self) -> None:
        repo = self.init_repo()
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Alpha Work",
            status="active",
            updated="2026-05-05",
        )

        install = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(install.returncode, 0, install.stderr)

        rewritten_commits = "\n".join(
            f"{index:040x} {index + 1:040x} refs/heads/topic" for index in range(10000)
        )
        hook_run = subprocess.run(
            [str(repo / ".git/hooks/post-rewrite"), "amend"],
            cwd=repo,
            check=False,
            text=True,
            input=rewritten_commits,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
        self.assertEqual(hook_run.returncode, 0, hook_run.stderr)
        self.assertTrue((repo / "docs/project_journal/INDEX.md").exists())

    def test_install_hooks_refuses_unmanaged_existing_hook(self) -> None:
        repo = self.init_repo()
        hook = repo / ".git/hooks/post-merge"
        hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        result = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not managed", result.stderr)

    def test_install_hooks_preflights_all_targets_before_writing(self) -> None:
        repo = self.init_repo()
        unmanaged = repo / ".git/hooks/post-checkout"
        unmanaged.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

        result = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((repo / ".git/hooks/post-merge").exists())
        exclude = (repo / ".git/info/exclude").read_text(encoding="utf-8")
        self.assertNotIn("docs/project_journal/INDEX.md", exclude.splitlines())

    def test_install_hooks_refuses_symlink_targets_before_writing(self) -> None:
        for name, target_exists in (
            ("existing-target", True),
            ("broken-target", False),
        ):
            with self.subTest(name=name):
                repo = self.init_repo(f"repo-{name}")
                target = self.root / f"{name}-external-hook"
                if target_exists:
                    target.write_text("#!/bin/sh\necho keep-me\n", encoding="utf-8")

                symlink = repo / ".git/hooks/post-checkout"
                symlink.symlink_to(target)

                result = self.run_cli("install-hooks", "--repo", str(repo))

                self.assertNotEqual(result.returncode, 0)
                self.assertIn("symlink", result.stderr)
                self.assertFalse((repo / ".git/hooks/post-merge").exists())
                exclude = (repo / ".git/info/exclude").read_text(encoding="utf-8")
                self.assertNotIn("docs/project_journal/INDEX.md", exclude.splitlines())
                if target_exists:
                    self.assertEqual(
                        target.read_text(encoding="utf-8"),
                        "#!/bin/sh\necho keep-me\n",
                    )

    def test_install_hooks_atomic_commit_rejects_racing_target_symlink(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        outside = self.root / "outside-hook"
        outside.write_text("keep-me\n", encoding="utf-8")
        actual_rename = project_journal._rename_hook_entry_with_flag
        raced = False

        def rename_with_target_race(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal raced
            if destination == "post-merge" and not raced:
                raced = True
                target = repo / ".githooks/post-merge"
                target.unlink()
                target.symlink_to(outside)
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_with_target_race,
        ):
            with self.assertRaises(
                project_journal._HookExchangeRecoveryRequired,
            ) as raised:
                project_journal.command_install_hooks(args)

        self.assertTrue(raced)
        installed = repo / ".githooks/post-merge"
        self.assertFalse(installed.is_symlink())
        self.assertIn(
            project_journal.HOOK_BEGIN,
            installed.read_text(encoding="utf-8"),
        )
        message = str(raised.exception)
        self.assertIn("recovery path_unverified", message)
        evidence = self.recovery_evidence(message)
        self.assertEqual(evidence["path_status"], "path_unverified")
        self.assertEqual(evidence["held_object_status"], "unreadable")
        self.assertEqual(evidence["leaf_observation_status"], "observed")
        self.assertIsInstance(evidence["leaf_identity"]["device"], int)
        self.assertIsInstance(evidence["leaf_identity"]["inode"], int)
        self.assertEqual(evidence["leaf_identity"]["type"], "0o120000")
        self.assertNotIn("preserved recovery locator", message)
        recoveries = list(
            (repo / ".githooks").glob(".project-journal-post-merge-*.tmp")
        )
        self.assertEqual(len(recoveries), 1)
        self.assertTrue(recoveries[0].is_symlink())
        self.assertEqual(recoveries[0].resolve(), outside.resolve())
        self.assertEqual(outside.read_text(encoding="utf-8"), "keep-me\n")

    def test_install_hooks_atomic_commit_rejects_racing_regular_target(
        self,
    ) -> None:
        for initially_exists in (False, True):
            with self.subTest(initially_exists=initially_exists):
                repo = self.init_repo(f"repo-regular-race-{initially_exists}")
                configured = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                if initially_exists:
                    first = self.run_cli("install-hooks", "--repo", str(repo))
                    self.assertEqual(first.returncode, 0, first.stderr)

                actual_rename = project_journal._rename_hook_entry_with_flag
                raced = False

                def rename_with_regular_target_race(
                    directory_fd: int,
                    source: str,
                    destination: str,
                    *,
                    exchange: bool,
                ) -> None:
                    nonlocal raced
                    if destination == "post-merge" and not raced:
                        raced = True
                        target = repo / ".githooks/post-merge"
                        if target.exists():
                            target.unlink()
                        target.write_text("racing installer\n", encoding="utf-8")
                    actual_rename(
                        directory_fd,
                        source,
                        destination,
                        exchange=exchange,
                    )

                args = mock.Mock(repo=str(repo))
                with mock.patch.object(
                    project_journal,
                    "_rename_hook_entry_with_flag",
                    side_effect=rename_with_regular_target_race,
                ):
                    with self.assertRaisesRegex(
                        project_journal.UserError,
                        "hook target changed at atomic commit",
                    ):
                        project_journal.command_install_hooks(args)

                self.assertTrue(raced)
                self.assertEqual(
                    (repo / ".githooks/post-merge").read_text(encoding="utf-8"),
                    "racing installer\n",
                )

    def test_install_hooks_preserves_recovery_on_rollback_failure(self) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        actual_rename = project_journal._rename_hook_entry_with_flag
        raced = False

        def rename_with_target_race(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal raced
            if destination == "post-merge" and not raced:
                raced = True
                target = repo / ".githooks/post-merge"
                target.unlink()
                target.write_text("racing installer\n", encoding="utf-8")
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_with_target_race,
        ):
            with mock.patch.object(
                project_journal,
                "_rollback_hook_exchange",
                return_value="injected rollback failure",
            ):
                with self.assertRaises(
                    project_journal._HookExchangeRecoveryRequired,
                ) as raised:
                    project_journal.command_install_hooks(args)

        self.assertIn("preserved recovery locator", str(raised.exception))
        recoveries = list(
            (repo / ".githooks").glob(".project-journal-post-merge-*.tmp")
        )
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(
            recoveries[0].read_text(encoding="utf-8"),
            "racing installer\n",
        )
        self.assertIn(
            project_journal.HOOK_BEGIN,
            (repo / ".githooks/post-merge").read_text(encoding="utf-8"),
        )

    def test_install_hooks_preserves_third_party_write_during_rollback(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        actual_rename = project_journal._rename_hook_entry_with_flag
        exchange_count = 0

        def rename_with_two_races(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal exchange_count
            if destination == "post-merge" and exchange:
                exchange_count += 1
                target = repo / ".githooks/post-merge"
                target.unlink()
                target.write_text(
                    (
                        "racing installer\n"
                        if exchange_count == 1
                        else "third-party rollback write\n"
                    ),
                    encoding="utf-8",
                )
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_with_two_races,
        ):
            with self.assertRaises(
                project_journal._HookExchangeRecoveryRequired,
            ) as raised:
                project_journal.command_install_hooks(args)

        self.assertEqual(exchange_count, 2)
        self.assertIn("preserved recovery locator", str(raised.exception))
        recoveries = list(
            (repo / ".githooks").glob(".project-journal-post-merge-*.tmp")
        )
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(
            recoveries[0].read_text(encoding="utf-8"),
            "third-party rollback write\n",
        )
        self.assertEqual(
            (repo / ".githooks/post-merge").read_text(encoding="utf-8"),
            "racing installer\n",
        )

    def test_install_hooks_preserves_displaced_hook_on_rename_then_interrupt(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        hook = repo / ".githooks/post-merge"
        old_content = hook.read_bytes().replace(
            project_journal.HOOK_BEGIN.encode(),
            (
                project_journal.HOOK_BEGIN + "\n# transaction-owned previous hook"
            ).encode(),
            1,
        )
        hook.write_bytes(old_content)
        old_stat = hook.stat()
        actual_rename = project_journal._rename_hook_entry_with_flag
        interrupted = False
        interruption = LegacyInterrupt("injected after committed exchange")
        original_args = interruption.args

        def rename_then_interrupt(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal interrupted
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )
            if destination == "post-merge" and exchange and not interrupted:
                interrupted = True
                raise interruption

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_then_interrupt,
        ):
            with self.assertRaises(LegacyInterrupt) as raised:
                project_journal.command_install_hooks(args)

        self.assertIs(raised.exception, interruption)
        self.assertEqual(raised.exception.args, original_args)
        self.assertTrue(interrupted)
        self.assertNotEqual(hook.read_bytes(), old_content)
        self.assertIn(project_journal.HOOK_BEGIN.encode(), hook.read_bytes())
        recoveries = list(
            (repo / ".githooks").glob(".project-journal-post-merge-*.tmp")
        )
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0].read_bytes(), old_content)
        recovery_stat = recoveries[0].stat()
        self.assertEqual(
            (recovery_stat.st_dev, recovery_stat.st_ino),
            (old_stat.st_dev, old_stat.st_ino),
        )
        self.assertTrue(
            any(
                "preserved recovery locator" in note
                for note in getattr(raised.exception, "__notes__", ())
            )
        )

    def test_install_hooks_preserves_displaced_hook_when_exchange_reports_error(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        hook = repo / ".githooks/post-merge"
        old_content = hook.read_bytes().replace(
            project_journal.HOOK_BEGIN.encode(),
            (
                project_journal.HOOK_BEGIN
                + "\n# hook displaced by error-reporting exchange"
            ).encode(),
            1,
        )
        hook.write_bytes(old_content)
        actual_rename = project_journal._rename_hook_entry_with_flag
        injected = False

        def exchange_then_report_error(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal injected
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )
            if destination == "post-merge" and exchange and not injected:
                injected = True
                raise OSError(errno.EIO, "injected post-exchange error")

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=exchange_then_report_error,
        ):
            with self.assertRaises(
                project_journal._HookExchangeRecoveryRequired,
            ) as raised:
                project_journal.command_install_hooks(args)

        self.assertTrue(injected)
        self.assertIn("preserved recovery locator", str(raised.exception))
        self.assertIn(
            "object-identity/content/access-policy verified", str(raised.exception)
        )
        self.assertIn(project_journal.HOOK_BEGIN.encode(), hook.read_bytes())
        self.assertNotEqual(hook.read_bytes(), old_content)
        recoveries = list(
            (repo / ".githooks").glob(".project-journal-post-merge-*.tmp")
        )
        self.assertEqual(len(recoveries), 1)
        self.assertEqual(recoveries[0].read_bytes(), old_content)

    def test_install_hook_cleanup_failure_preserves_each_primary_stage_error(
        self,
    ) -> None:
        cases = (
            (
                "write",
                LegacyInterrupt("original hook write interruption"),
                PermissionError(
                    errno.EACCES,
                    "simulated hook write cleanup denial",
                ),
            ),
            (
                "validation",
                LegacyUnsupportedPlatform(
                    "original staged hook validation failure",
                ),
                OSError(
                    errno.EIO,
                    "simulated hook validation cleanup failure",
                ),
            ),
            (
                "commit",
                LegacyInterrupt("original hook commit interruption"),
                LegacyInterrupt("simulated hook commit cleanup interruption"),
            ),
        )
        actual_snapshot = project_journal._snapshot_hook_target
        actual_unlink = project_journal.os.unlink

        for stage, original_error, cleanup_error in cases:
            with self.subTest(stage=stage):
                repo = self.init_repo(f"repo-hook-cleanup-{stage}")
                configured = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                stage_injected = False
                cleanup_injected = False
                original_args = original_error.args

                def fail_write(*_args: object, **_kwargs: object) -> None:
                    nonlocal stage_injected
                    stage_injected = True
                    raise original_error

                def fail_validation(
                    binding: project_journal._HookDirectoryBinding,
                    name: str,
                ) -> tuple[project_journal._HookTargetSnapshot, bytes | None]:
                    nonlocal stage_injected
                    if (
                        name.startswith(".project-journal-")
                        and name.endswith(".tmp")
                        and not stage_injected
                    ):
                        stage_injected = True
                        raise original_error
                    return actual_snapshot(binding, name)

                def fail_commit(*_args: object, **_kwargs: object) -> None:
                    nonlocal stage_injected
                    stage_injected = True
                    raise original_error

                if stage == "write":
                    stage_patch = mock.patch.object(
                        project_journal,
                        "_write_all",
                        side_effect=fail_write,
                    )
                    expected_traceback_name = "fail_write"
                elif stage == "validation":
                    stage_patch = mock.patch.object(
                        project_journal,
                        "_snapshot_hook_target",
                        side_effect=fail_validation,
                    )
                    expected_traceback_name = "fail_validation"
                else:
                    stage_patch = mock.patch.object(
                        project_journal,
                        "_commit_hook_target_atomically",
                        side_effect=fail_commit,
                    )
                    expected_traceback_name = "fail_commit"

                def fail_temporary_unlink(
                    path: os.PathLike[str] | str,
                    *args: object,
                    dir_fd: int | None = None,
                    **kwargs: object,
                ) -> None:
                    nonlocal cleanup_injected
                    leaf = os.fsdecode(os.fspath(path))
                    if (
                        leaf.startswith(".project-journal-")
                        and leaf.endswith(".tmp")
                        and not cleanup_injected
                    ):
                        cleanup_injected = True
                        raise cleanup_error
                    if dir_fd is None:
                        actual_unlink(path, *args, **kwargs)
                    else:
                        actual_unlink(path, *args, dir_fd=dir_fd, **kwargs)

                args = mock.Mock(repo=str(repo))
                with stage_patch:
                    with mock.patch.object(
                        project_journal.os,
                        "unlink",
                        side_effect=fail_temporary_unlink,
                    ):
                        try:
                            project_journal.command_install_hooks(args)
                        except BaseException as exc:
                            raised_error = exc
                        else:
                            self.fail(f"expected original {stage} failure")

                self.assertIs(raised_error, original_error)
                self.assertIs(type(raised_error), type(original_error))
                self.assertEqual(raised_error.args, original_args)
                if isinstance(original_error, LegacyUnsupportedPlatform):
                    self.assertEqual(
                        raised_error.code,
                        project_journal.UnsupportedPlatform.code,
                    )
                exception_notes = getattr(raised_error, "__notes__", ())
                notes = "\n".join(exception_notes)
                self.assertIn("hook temporary-entry cleanup-incomplete", notes)
                self.assertIn("descriptor-bound/path-verified", notes)
                self.assertIn(str(cleanup_error), notes)
                recovery_note = next(
                    detail
                    for detail in exception_notes
                    if "recovery_evidence=" in detail
                )
                evidence = self.recovery_evidence(recovery_note)
                self.assertEqual(evidence["path_status"], "path_verified")
                self.assertEqual(evidence["held_object_status"], "verified")
                traceback_names = self.exception_traceback_names(raised_error)
                self.assertIn(expected_traceback_name, traceback_names)
                self.assertNotIn(
                    "_cleanup_hook_temporary_entry",
                    traceback_names,
                )
                self.assertTrue(stage_injected)
                self.assertTrue(cleanup_injected)
                recoveries = list(
                    (repo / ".githooks").glob(".project-journal-post-merge-*.tmp")
                )
                self.assertEqual(len(recoveries), 1)
                self.assertEqual(evidence["path"], str(recoveries[0].resolve()))
                recoveries[0].unlink()

    def test_hook_cleanup_without_primary_reports_its_own_failure(self) -> None:
        repo = self.init_repo("repo-standalone-hook-cleanup").resolve()
        hooks = repo / ".githooks"
        hooks.mkdir()
        temporary_name = ".project-journal-post-merge-standalone.tmp"
        temporary = hooks / temporary_name
        temporary.write_text("staged hook\n", encoding="utf-8")
        temporary.chmod(0o600)
        binding = project_journal._bind_hook_directory(
            repo,
            project_journal._HookPathPlan(
                root=repo,
                components=(".githooks",),
            ),
        )
        cleanup_error = PermissionError(
            errno.EACCES,
            "simulated standalone hook cleanup denial",
        )
        actual_unlink = project_journal.os.unlink

        def fail_temporary_unlink(
            path: os.PathLike[str] | str,
            *args: object,
            dir_fd: int | None = None,
            **kwargs: object,
        ) -> None:
            if os.fsdecode(os.fspath(path)) == temporary_name:
                raise cleanup_error
            if dir_fd is None:
                actual_unlink(path, *args, **kwargs)
            else:
                actual_unlink(path, *args, dir_fd=dir_fd, **kwargs)

        try:
            with mock.patch.object(
                project_journal.os,
                "unlink",
                side_effect=fail_temporary_unlink,
            ):
                with self.assertRaises(project_journal.UserError) as raised:
                    project_journal._cleanup_hook_temporary_entry(
                        binding,
                        temporary_name,
                        None,
                    )

            self.assertIs(raised.exception.__cause__, cleanup_error)
            message = str(raised.exception)
            self.assertIn("hook temporary-entry cleanup-incomplete", message)
            self.assertIn("descriptor-bound/path-verified", message)
            evidence = self.recovery_evidence(message)
            self.assertEqual(evidence["path_status"], "path_verified")
            self.assertEqual(evidence["path"], str(temporary))
            self.assertTrue(temporary.exists())

            cleanup_interrupt = LegacyInterrupt(
                "simulated standalone hook cleanup interruption",
            )
            cleanup_error = cleanup_interrupt
            try:
                with mock.patch.object(
                    project_journal.os,
                    "unlink",
                    side_effect=fail_temporary_unlink,
                ):
                    project_journal._cleanup_hook_temporary_entry(
                        binding,
                        temporary_name,
                        None,
                    )
            except LegacyInterrupt as exc:
                raised_interrupt = exc
            else:
                self.fail("expected standalone hook cleanup interruption")

            self.assertIs(raised_interrupt, cleanup_interrupt)
            interrupt_notes = "\n".join(getattr(raised_interrupt, "__notes__", ()))
            self.assertIn("hook temporary-entry cleanup-incomplete", interrupt_notes)
            self.assertIn("descriptor-bound/path-verified", interrupt_notes)
            self.assertTrue(temporary.exists())
        finally:
            try:
                actual_unlink(temporary_name, dir_fd=binding.fd)
            except FileNotFoundError:
                pass
            project_journal._close_hook_binding(binding)

    def test_install_hooks_cleans_staged_hook_only_after_uncommitted_exchange_error(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        hook = repo / ".githooks/post-merge"
        old_content = hook.read_bytes()
        actual_rename = project_journal._rename_hook_entry_with_flag
        injected = False

        def report_error_without_exchange(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal injected
            if destination == "post-merge" and exchange and not injected:
                injected = True
                raise OSError(errno.EIO, "injected pre-exchange error")
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=report_error_without_exchange,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "injected pre-exchange error",
            ):
                project_journal.command_install_hooks(args)

        self.assertTrue(injected)
        self.assertEqual(hook.read_bytes(), old_content)
        self.assertEqual(
            list((repo / ".githooks").glob(".project-journal-post-merge-*.tmp")),
            [],
        )

    def test_install_hooks_reports_committed_state_on_interrupt_after_unlink(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        hook = repo / ".githooks/post-merge"
        old_content = hook.read_bytes().replace(
            project_journal.HOOK_BEGIN.encode(),
            (
                project_journal.HOOK_BEGIN + "\n# transaction-owned previous hook"
            ).encode(),
            1,
        )
        hook.write_bytes(old_content)
        actual_unlink = project_journal.os.unlink
        interrupted = False
        interruption = LegacyInterrupt("injected after displaced-hook unlink")
        original_args = interruption.args

        def unlink_then_interrupt(
            path: os.PathLike[str] | str,
            *,
            dir_fd: int | None = None,
        ) -> None:
            nonlocal interrupted
            actual_unlink(path, dir_fd=dir_fd)
            if (
                isinstance(path, str)
                and path.startswith(".project-journal-post-merge-")
                and not interrupted
            ):
                interrupted = True
                raise interruption

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal.os,
            "unlink",
            side_effect=unlink_then_interrupt,
        ):
            with self.assertRaises(LegacyInterrupt) as raised:
                project_journal.command_install_hooks(args)

        self.assertIs(raised.exception, interruption)
        self.assertEqual(raised.exception.args, original_args)
        self.assertTrue(interrupted)
        self.assertNotEqual(hook.read_bytes(), old_content)
        self.assertIn(project_journal.HOOK_BEGIN.encode(), hook.read_bytes())
        self.assertEqual(
            list((repo / ".githooks").glob(".project-journal-post-merge-*.tmp")),
            [],
        )
        notes = getattr(raised.exception, "__notes__", ())
        self.assertTrue(
            any("installed-target-committed state" in note for note in notes),
        )
        self.assertTrue(any("cleanup completed" in note for note in notes))
        self.assertFalse(any("recovery locator" in note for note in notes))

    def test_install_hooks_reports_committed_absent_target_on_rename_interrupt(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        actual_rename = project_journal._rename_hook_entry_with_flag
        interrupted = False
        interruption = LegacyInterrupt(
            "injected after committed no-replace rename",
        )
        original_args = interruption.args

        def rename_then_interrupt(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal interrupted
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )
            if destination == "post-merge" and not exchange and not interrupted:
                interrupted = True
                raise interruption

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_then_interrupt,
        ):
            with self.assertRaises(LegacyInterrupt) as raised:
                project_journal.command_install_hooks(args)

        self.assertIs(raised.exception, interruption)
        self.assertEqual(raised.exception.args, original_args)
        self.assertTrue(interrupted)
        hook = repo / ".githooks/post-merge"
        self.assertIn(
            project_journal.HOOK_BEGIN,
            hook.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            list((repo / ".githooks").glob(".project-journal-post-merge-*.tmp")),
            [],
        )
        notes = "\n".join(getattr(raised.exception, "__notes__", ()))
        self.assertIn("no-replace rename committed", notes)
        self.assertIn("object-identity/content/access-policy verified", notes)
        self.assertIn("no displaced-hook recovery object exists", notes)
        self.assertNotIn("recovery locator", notes)

    def test_install_hooks_continues_after_committed_absent_rename_reports_eio(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        actual_rename = project_journal._rename_hook_entry_with_flag
        injected = False

        def rename_then_report_error(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal injected
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )
            if destination == "post-merge" and not exchange and not injected:
                injected = True
                raise OSError(errno.EIO, "injected post-rename error")

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_then_report_error,
        ):
            result = project_journal.command_install_hooks(args)

        self.assertEqual(result, 0)
        self.assertTrue(injected)
        hook = repo / ".githooks/post-merge"
        self.assertIn(
            project_journal.HOOK_BEGIN,
            hook.read_text(encoding="utf-8"),
        )
        self.assertEqual(
            list((repo / ".githooks").glob(".project-journal-post-merge-*.tmp")),
            [],
        )

    def test_install_hooks_cleans_staged_absent_hook_after_proven_uncommitted_eio(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        actual_rename = project_journal._rename_hook_entry_with_flag
        injected = False

        def report_error_before_rename(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal injected
            if destination == "post-merge" and not exchange and not injected:
                injected = True
                raise OSError(errno.EIO, "injected pre-rename error")
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=report_error_before_rename,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "injected pre-rename error",
            ):
                project_journal.command_install_hooks(args)

        self.assertTrue(injected)
        self.assertFalse((repo / ".githooks/post-merge").exists())
        self.assertEqual(
            list((repo / ".githooks").glob(".project-journal-post-merge-*.tmp")),
            [],
        )

    def test_install_hooks_preserves_locator_for_uncertain_absent_rename_eio(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        actual_rename = project_journal._rename_hook_entry_with_flag
        injected = False

        def race_target_then_report_error(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal injected
            if destination == "post-merge" and not exchange and not injected:
                injected = True
                (repo / ".githooks/post-merge").write_text(
                    "third-party hook\n",
                    encoding="utf-8",
                )
                raise OSError(errno.EIO, "injected uncertain rename error")
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=race_target_then_report_error,
        ):
            with self.assertRaises(
                project_journal._HookExchangeRecoveryRequired,
            ) as raised:
                project_journal.command_install_hooks(args)

        self.assertTrue(injected)
        self.assertEqual(
            (repo / ".githooks/post-merge").read_text(encoding="utf-8"),
            "third-party hook\n",
        )
        message = str(raised.exception)
        self.assertIn("state is uncertain", message)
        self.assertIn("installed target mismatches", message)
        self.assertIn(
            "staged temporary remains object-identity/content/access-policy verified",
            message,
        )
        self.assertIn("preserved recovery locator", message)
        recoveries = list(
            (repo / ".githooks").glob(".project-journal-post-merge-*.tmp")
        )
        self.assertEqual(len(recoveries), 1)
        self.assertIn(project_journal.HOOK_BEGIN.encode(), recoveries[0].read_bytes())

    def test_install_hooks_reports_post_commit_directory_fsync_failure(
        self,
    ) -> None:
        for initially_exists in (False, True):
            with self.subTest(initially_exists=initially_exists):
                repo = self.init_repo(f"repo-fsync-{initially_exists}")
                configured = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                if initially_exists:
                    first = self.run_cli("install-hooks", "--repo", str(repo))
                    self.assertEqual(first.returncode, 0, first.stderr)

                actual_commit = project_journal._commit_hook_target_atomically
                actual_fsync = project_journal.os.fsync
                commit_returned = False

                def commit_and_mark(
                    binding: project_journal._HookDirectoryBinding,
                    target: project_journal._HookTargetSnapshot,
                    temporary_name: str,
                    staged: project_journal._HookTargetSnapshot,
                    commit_state: project_journal._HookCommitState,
                ) -> None:
                    nonlocal commit_returned
                    actual_commit(
                        binding,
                        target,
                        temporary_name,
                        staged,
                        commit_state,
                    )
                    if target.name == "post-merge":
                        commit_returned = True

                def fail_post_commit_directory_fsync(fd: int) -> None:
                    if commit_returned and stat.S_ISDIR(os.fstat(fd).st_mode):
                        raise OSError("injected post-commit directory fsync failure")
                    actual_fsync(fd)

                args = mock.Mock(repo=str(repo))
                with mock.patch.object(
                    project_journal,
                    "_commit_hook_target_atomically",
                    side_effect=commit_and_mark,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "fsync",
                        side_effect=fail_post_commit_directory_fsync,
                    ):
                        with self.assertRaises(
                            project_journal.UserError,
                        ) as raised:
                            project_journal.command_install_hooks(args)

                message = str(raised.exception)
                self.assertIn("hook target installation committed", message)
                self.assertIn("directory durability is incomplete", message)
                self.assertIn(
                    "injected post-commit directory fsync failure",
                    message,
                )
                self.assertIn(
                    "object-identity/content/access-policy verified",
                    message,
                )
                self.assertNotIn("failed to install hook", message)
                self.assertNotIn("recovery locator", message)
                self.assertIn(
                    project_journal.HOOK_BEGIN,
                    (repo / ".githooks/post-merge").read_text(encoding="utf-8"),
                )

    def test_install_hooks_reports_post_commit_verification_failure(
        self,
    ) -> None:
        for initially_exists in (False, True):
            with self.subTest(initially_exists=initially_exists):
                repo = self.init_repo(f"repo-verify-{initially_exists}")
                configured = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                if initially_exists:
                    first = self.run_cli("install-hooks", "--repo", str(repo))
                    self.assertEqual(first.returncode, 0, first.stderr)

                actual_commit = project_journal._commit_hook_target_atomically
                actual_snapshot = project_journal._snapshot_hook_target
                commit_returned = False
                injected = False

                def commit_and_mark(
                    binding: project_journal._HookDirectoryBinding,
                    target: project_journal._HookTargetSnapshot,
                    temporary_name: str,
                    staged: project_journal._HookTargetSnapshot,
                    commit_state: project_journal._HookCommitState,
                ) -> None:
                    nonlocal commit_returned
                    actual_commit(
                        binding,
                        target,
                        temporary_name,
                        staged,
                        commit_state,
                    )
                    if target.name == "post-merge":
                        commit_returned = True

                def fail_final_snapshot(
                    binding: project_journal._HookDirectoryBinding,
                    name: str,
                ) -> tuple[project_journal._HookTargetSnapshot, bytes | None]:
                    nonlocal injected
                    if commit_returned and name == "post-merge" and not injected:
                        injected = True
                        raise project_journal.UserError(
                            "injected final installed-target verification failure"
                        )
                    return actual_snapshot(binding, name)

                args = mock.Mock(repo=str(repo))
                with mock.patch.object(
                    project_journal,
                    "_commit_hook_target_atomically",
                    side_effect=commit_and_mark,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_snapshot_hook_target",
                        side_effect=fail_final_snapshot,
                    ):
                        with self.assertRaises(
                            project_journal.UserError,
                        ) as raised:
                            project_journal.command_install_hooks(args)

                self.assertTrue(injected)
                message = str(raised.exception)
                self.assertIn("hook target installation committed", message)
                self.assertIn(
                    "final installed-target verification is incomplete",
                    message,
                )
                self.assertIn(
                    "injected final installed-target verification failure",
                    message,
                )
                self.assertIn(
                    "object-identity/content/access-policy verified",
                    message,
                )
                self.assertNotIn("recovery locator", message)

    def test_install_hooks_detects_directory_replacement_during_final_snapshot(
        self,
    ) -> None:
        for initially_exists in (False, True):
            with self.subTest(initially_exists=initially_exists):
                repo = self.init_repo(f"repo-final-binding-{initially_exists}")
                configured = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                if initially_exists:
                    first = self.run_cli("install-hooks", "--repo", str(repo))
                    self.assertEqual(first.returncode, 0, first.stderr)

                hooks_dir = repo / ".githooks"
                moved_hooks = repo / ".githooks-validated-object"
                actual_commit = project_journal._commit_hook_target_atomically
                actual_snapshot = project_journal._snapshot_hook_target
                actual_mark_verified = project_journal._HookCommitState.mark_verified
                commit_returned = False
                raced_commit_state: project_journal._HookCommitState | None = None
                raced = False
                raced_target_verified = False

                def commit_and_mark(
                    binding: project_journal._HookDirectoryBinding,
                    target: project_journal._HookTargetSnapshot,
                    temporary_name: str,
                    staged: project_journal._HookTargetSnapshot,
                    commit_state: project_journal._HookCommitState,
                ) -> None:
                    nonlocal commit_returned, raced_commit_state
                    actual_commit(
                        binding,
                        target,
                        temporary_name,
                        staged,
                        commit_state,
                    )
                    if target.name == "post-rewrite":
                        commit_returned = True
                        raced_commit_state = commit_state

                def replace_directory_during_final_snapshot(
                    binding: project_journal._HookDirectoryBinding,
                    name: str,
                ) -> tuple[project_journal._HookTargetSnapshot, bytes | None]:
                    nonlocal raced
                    snapshot = actual_snapshot(binding, name)
                    if commit_returned and name == "post-rewrite" and not raced:
                        raced = True
                        hooks_dir.rename(moved_hooks)
                        hooks_dir.mkdir()
                    return snapshot

                def record_mark_verified(
                    commit_state: project_journal._HookCommitState,
                ) -> None:
                    nonlocal raced_target_verified
                    if commit_state is raced_commit_state:
                        raced_target_verified = True
                    actual_mark_verified(commit_state)

                args = mock.Mock(repo=str(repo))
                with mock.patch.object(
                    project_journal,
                    "_commit_hook_target_atomically",
                    side_effect=commit_and_mark,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_snapshot_hook_target",
                        side_effect=replace_directory_during_final_snapshot,
                    ):
                        with mock.patch.object(
                            project_journal._HookCommitState,
                            "mark_verified",
                            autospec=True,
                            side_effect=record_mark_verified,
                        ):
                            with self.assertRaisesRegex(
                                project_journal.UserError,
                                "ancestor identity or access policy changed",
                            ) as raised:
                                project_journal.command_install_hooks(args)

                self.assertTrue(raced)
                self.assertFalse(raced_target_verified)
                self.assertIn(
                    "final effective hook destination verification is incomplete",
                    str(raised.exception),
                )
                self.assertFalse((hooks_dir / "post-rewrite").exists())
                self.assertIn(
                    project_journal.HOOK_BEGIN,
                    (moved_hooks / "post-rewrite").read_text(encoding="utf-8"),
                )

    def test_install_hooks_detects_effective_destination_drift_after_final_snapshot(
        self,
    ) -> None:
        for mode, initially_exists in (
            ("local", False),
            ("local-existing", True),
            ("include", False),
            ("worktree", False),
        ):
            with self.subTest(mode=mode):
                repo = self.init_repo(f"repo-effective-drift-{mode}")
                included = repo / ".git/hooks.inc"
                if mode == "include":
                    included.write_text(
                        "[core]\n    hooksPath = .githooks-a\n",
                        encoding="utf-8",
                    )
                    configured = run_git(
                        repo,
                        "config",
                        "--local",
                        "include.path",
                        "hooks.inc",
                    )
                elif mode == "worktree":
                    configured = run_git(
                        repo,
                        "config",
                        "extensions.worktreeConfig",
                        "true",
                    )
                    self.assertEqual(configured.returncode, 0, configured.stderr)
                    configured = run_git(
                        repo,
                        "config",
                        "--worktree",
                        "core.hooksPath",
                        ".githooks-a",
                    )
                else:
                    configured = run_git(
                        repo,
                        "config",
                        "--local",
                        "core.hooksPath",
                        ".githooks-a",
                    )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                if initially_exists:
                    first = self.run_cli("install-hooks", "--repo", str(repo))
                    self.assertEqual(first.returncode, 0, first.stderr)

                actual_commit = project_journal._commit_hook_target_atomically
                actual_snapshot = project_journal._snapshot_hook_target
                actual_mark_verified = project_journal._HookCommitState.mark_verified
                commit_returned = False
                raced_commit_state: project_journal._HookCommitState | None = None
                drifted = False
                drifted_target_verified = False

                def commit_and_mark(
                    binding: project_journal._HookDirectoryBinding,
                    target: project_journal._HookTargetSnapshot,
                    temporary_name: str,
                    staged: project_journal._HookTargetSnapshot,
                    commit_state: project_journal._HookCommitState,
                ) -> None:
                    nonlocal commit_returned, raced_commit_state
                    self.assertEqual(binding.repo, repo.resolve())
                    self.assertEqual(
                        binding.plan.path,
                        repo.resolve() / ".githooks-a",
                    )
                    actual_commit(
                        binding,
                        target,
                        temporary_name,
                        staged,
                        commit_state,
                    )
                    if target.name == "post-rewrite":
                        commit_returned = True
                        raced_commit_state = commit_state

                def drift_after_final_snapshot(
                    binding: project_journal._HookDirectoryBinding,
                    name: str,
                ) -> tuple[project_journal._HookTargetSnapshot, bytes | None]:
                    nonlocal drifted
                    snapshot = actual_snapshot(binding, name)
                    if commit_returned and name == "post-rewrite" and not drifted:
                        drifted = True
                        if mode == "include":
                            included.write_text(
                                "[core]\n    hooksPath = .githooks-b\n",
                                encoding="utf-8",
                            )
                            changed = subprocess.CompletedProcess(
                                args=[],
                                returncode=0,
                                stdout="",
                                stderr="",
                            )
                        elif mode == "worktree":
                            changed = run_git(
                                repo,
                                "config",
                                "--worktree",
                                "core.hooksPath",
                                ".githooks-b",
                            )
                        else:
                            changed = run_git(
                                repo,
                                "config",
                                "--local",
                                "core.hooksPath",
                                ".githooks-b",
                            )
                        self.assertEqual(changed.returncode, 0, changed.stderr)
                    return snapshot

                def record_mark_verified(
                    commit_state: project_journal._HookCommitState,
                ) -> None:
                    nonlocal drifted_target_verified
                    if commit_state is raced_commit_state:
                        drifted_target_verified = True
                    actual_mark_verified(commit_state)

                args = mock.Mock(repo=str(repo))
                with mock.patch.object(
                    project_journal,
                    "_commit_hook_target_atomically",
                    side_effect=commit_and_mark,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_snapshot_hook_target",
                        side_effect=drift_after_final_snapshot,
                    ):
                        with mock.patch.object(
                            project_journal._HookCommitState,
                            "mark_verified",
                            autospec=True,
                            side_effect=record_mark_verified,
                        ):
                            with self.assertRaises(
                                project_journal.EffectiveHookDestinationChanged
                            ) as raised:
                                project_journal.command_install_hooks(args)

                self.assertTrue(drifted)
                self.assertFalse(drifted_target_verified)
                message = str(raised.exception)
                self.assertIn("effective_hook_destination_changed", message)
                self.assertIn("hook target installation committed", message)
                self.assertIn(
                    "final effective hook destination verification is incomplete",
                    message,
                )
                self.assertTrue((repo / ".githooks-a/post-rewrite").exists())
                self.assertFalse((repo / ".githooks-b/post-rewrite").exists())

    def test_install_hooks_accepts_equivalent_effective_destination_change(
        self,
    ) -> None:
        repo = self.init_repo("repo-equivalent-effective-destination")
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        actual_commit = project_journal._commit_hook_target_atomically
        actual_snapshot = project_journal._snapshot_hook_target
        commit_returned = False
        changed = False

        def commit_and_mark(
            binding: project_journal._HookDirectoryBinding,
            target: project_journal._HookTargetSnapshot,
            temporary_name: str,
            staged: project_journal._HookTargetSnapshot,
            commit_state: project_journal._HookCommitState,
        ) -> None:
            nonlocal commit_returned
            actual_commit(
                binding,
                target,
                temporary_name,
                staged,
                commit_state,
            )
            if target.name == "post-rewrite":
                commit_returned = True

        def change_text_after_final_snapshot(
            binding: project_journal._HookDirectoryBinding,
            name: str,
        ) -> tuple[project_journal._HookTargetSnapshot, bytes | None]:
            nonlocal changed
            snapshot = actual_snapshot(binding, name)
            if commit_returned and name == "post-rewrite" and not changed:
                changed = True
                worktree_enabled = run_git(
                    repo,
                    "config",
                    "extensions.worktreeConfig",
                    "true",
                )
                self.assertEqual(
                    worktree_enabled.returncode,
                    0,
                    worktree_enabled.stderr,
                )
                worktree_override = run_git(
                    repo,
                    "config",
                    "--worktree",
                    "core.hooksPath",
                    "./.githooks",
                )
                self.assertEqual(
                    worktree_override.returncode,
                    0,
                    worktree_override.stderr,
                )
            return snapshot

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_commit_hook_target_atomically",
            side_effect=commit_and_mark,
        ):
            with mock.patch.object(
                project_journal,
                "_snapshot_hook_target",
                side_effect=change_text_after_final_snapshot,
            ):
                installed = project_journal.command_install_hooks(args)

        self.assertEqual(installed, 0)
        self.assertTrue(changed)
        for name in project_journal.HOOK_NAMES:
            self.assertTrue((repo / ".githooks" / name).exists())

    def test_effective_hook_query_failures_remain_unverified(self) -> None:
        repo = self.init_repo("repo-effective-query-failures").resolve()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        binding = project_journal._preflight_hook_targets(repo)
        try:
            cases = (
                subprocess.CompletedProcess(
                    args=[],
                    returncode=0,
                    stdout=b"malformed\0",
                    stderr=b"",
                ),
                subprocess.CompletedProcess(
                    args=[],
                    returncode=9,
                    stdout=b"",
                    stderr=b"",
                ),
                project_journal.UserError(
                    "effective core.hooksPath query timed out after 5 seconds"
                ),
            )
            for result in cases:
                with self.subTest(result=result):
                    patch_value = (
                        mock.patch.object(
                            project_journal,
                            "_capture_bounded_process",
                            side_effect=result,
                        )
                        if isinstance(result, BaseException)
                        else mock.patch.object(
                            project_journal,
                            "_capture_bounded_process",
                            return_value=result,
                        )
                    )
                    with patch_value:
                        with self.assertRaises(
                            project_journal.EffectiveHookConfigurationUnverified
                        ) as raised:
                            project_journal._revalidate_effective_hook_destination(
                                binding
                            )
                self.assertIn(
                    "effective_hook_configuration_unverified",
                    str(raised.exception),
                )
        finally:
            project_journal._close_hook_binding(binding)

    def test_effective_hook_configuration_wrapper_preserves_source_notes(
        self,
    ) -> None:
        note = (
            "Git launch cleanup-incomplete; retained launch locator "
            "/tmp/project-journal-launch"
        )
        source = project_journal.UserError("injected hook query failure")
        project_journal._add_exception_detail(source, note)
        binding = mock.Mock(
            repo=self.root,
            plan=project_journal._HookPathPlan(
                root=self.root,
                components=(".githooks",),
            ),
        )

        with mock.patch.object(
            project_journal,
            "_hook_path_plan",
            side_effect=source,
        ):
            with self.assertRaises(
                project_journal.EffectiveHookConfigurationUnverified
            ) as raised:
                project_journal._revalidate_effective_hook_destination(binding)

        self.assertEqual(
            raised.exception.code, "effective_hook_configuration_unverified"
        )
        self.assertEqual(getattr(raised.exception, "__notes__", ()), [note])
        self.assertIs(raised.exception.__cause__, source)

    def test_post_commit_effective_hook_wrapper_preserves_notes(self) -> None:
        repo = self.init_repo("repo-post-commit-effective-notes").resolve()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        binding = project_journal._preflight_hook_targets(repo)
        actual_hook_path_plan = project_journal._hook_path_plan
        note = (
            "Git launch cleanup-incomplete; retained launch locator "
            "/tmp/project-journal-launch"
        )
        query_count = 0
        source: project_journal.UserError | None = None

        def fail_post_commit_query(
            selected_repo: pathlib.Path,
            *,
            deadline: float | None = None,
            deadline_error: str = "hook-path discovery exceeded its shared deadline",
        ) -> project_journal._HookPathPlan:
            nonlocal query_count, source
            query_count += 1
            if query_count == 2:
                source = project_journal.UserError(
                    "injected post-commit hook query failure"
                )
                project_journal._add_exception_detail(source, note)
                raise source
            return actual_hook_path_plan(
                selected_repo,
                deadline=deadline,
                deadline_error=deadline_error,
            )

        try:
            with mock.patch.object(
                project_journal,
                "_hook_path_plan",
                side_effect=fail_post_commit_query,
            ):
                with self.assertRaises(
                    project_journal.EffectiveHookConfigurationUnverified
                ) as raised:
                    project_journal._install_hook(
                        binding,
                        binding.targets[0],
                    )
        finally:
            project_journal._close_hook_binding(binding)

        self.assertEqual(query_count, 2)
        self.assertEqual(
            raised.exception.code, "effective_hook_configuration_unverified"
        )
        self.assertEqual(getattr(raised.exception, "__notes__", ()), [note])
        first_wrapper = raised.exception.__cause__
        self.assertIsInstance(
            first_wrapper,
            project_journal.EffectiveHookConfigurationUnverified,
        )
        self.assertEqual(getattr(first_wrapper, "__notes__", ()), [note])
        self.assertIs(first_wrapper.__cause__, source)
        self.assertIn("hook target installation committed", str(raised.exception))
        self.assertIn(
            "final effective hook destination verification is incomplete",
            str(raised.exception),
        )
        self.assertTrue((repo / ".githooks/post-merge").exists())

    def test_effective_destination_drift_before_commit_cleans_staged_hook(
        self,
    ) -> None:
        repo = self.init_repo("repo-effective-precommit-drift")
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks-a",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        actual_snapshot = project_journal._snapshot_hook_target
        drifted = False

        def drift_after_staged_snapshot(
            binding: project_journal._HookDirectoryBinding,
            name: str,
        ) -> tuple[project_journal._HookTargetSnapshot, bytes | None]:
            nonlocal drifted
            snapshot = actual_snapshot(binding, name)
            if name.startswith(".project-journal-post-merge-") and not drifted:
                drifted = True
                changed = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks-b",
                )
                self.assertEqual(changed.returncode, 0, changed.stderr)
            return snapshot

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_snapshot_hook_target",
            side_effect=drift_after_staged_snapshot,
        ):
            with mock.patch.object(
                project_journal,
                "_commit_hook_target_atomically",
            ) as commit:
                with self.assertRaises(project_journal.EffectiveHookDestinationChanged):
                    project_journal.command_install_hooks(args)

        self.assertTrue(drifted)
        commit.assert_not_called()
        self.assertFalse((repo / ".githooks-a/post-merge").exists())
        self.assertFalse((repo / ".githooks-b/post-merge").exists())
        self.assertEqual(
            list((repo / ".githooks-a").glob(".project-journal-*.tmp")),
            [],
        )

    def test_install_hooks_reports_post_commit_verification_interrupt(
        self,
    ) -> None:
        for initially_exists in (False, True):
            with self.subTest(initially_exists=initially_exists):
                repo = self.init_repo(f"repo-interrupt-{initially_exists}")
                configured = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                if initially_exists:
                    first = self.run_cli("install-hooks", "--repo", str(repo))
                    self.assertEqual(first.returncode, 0, first.stderr)

                actual_commit = project_journal._commit_hook_target_atomically
                actual_snapshot = project_journal._snapshot_hook_target
                commit_returned = False
                interrupted = False

                def commit_and_mark(
                    binding: project_journal._HookDirectoryBinding,
                    target: project_journal._HookTargetSnapshot,
                    temporary_name: str,
                    staged: project_journal._HookTargetSnapshot,
                    commit_state: project_journal._HookCommitState,
                ) -> None:
                    nonlocal commit_returned
                    actual_commit(
                        binding,
                        target,
                        temporary_name,
                        staged,
                        commit_state,
                    )
                    if target.name == "post-merge":
                        commit_returned = True

                def interrupt_final_snapshot(
                    binding: project_journal._HookDirectoryBinding,
                    name: str,
                ) -> tuple[project_journal._HookTargetSnapshot, bytes | None]:
                    nonlocal interrupted
                    if commit_returned and name == "post-merge" and not interrupted:
                        interrupted = True
                        raise KeyboardInterrupt(
                            "injected final installed-target verification interrupt"
                        )
                    return actual_snapshot(binding, name)

                args = mock.Mock(repo=str(repo))
                with mock.patch.object(
                    project_journal,
                    "_commit_hook_target_atomically",
                    side_effect=commit_and_mark,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_snapshot_hook_target",
                        side_effect=interrupt_final_snapshot,
                    ):
                        with self.assertRaises(KeyboardInterrupt) as raised:
                            project_journal.command_install_hooks(args)

                self.assertTrue(interrupted)
                notes = "\n".join(getattr(raised.exception, "__notes__", ()))
                self.assertIn("hook target installation committed", notes)
                self.assertIn(
                    "final installed-target verification is incomplete "
                    "after interruption",
                    notes,
                )
                self.assertIn(
                    "object-identity/content/access-policy verified",
                    notes,
                )
                self.assertNotIn("recovery locator", notes)

    def test_install_hooks_final_verified_window_propagates_deferred_signal_cleanly(
        self,
    ) -> None:
        signums = tuple(
            getattr(project_journal.signal, name)
            for name in ("SIGHUP", "SIGTERM", "SIGQUIT")
            if hasattr(project_journal.signal, name)
        )
        for signum in signums:
            with self.subTest(signal=project_journal._signal_name(signum)):
                repo = self.init_repo(
                    "repo-final-verified-"
                    + project_journal._signal_name(signum).lower()
                )
                configured = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                actual_mark_verified = project_journal._HookCommitState.mark_verified
                verified_state: project_journal._HookCommitState | None = None
                interruption = project_journal._DeferredTermination(signum)

                def mark_verified_and_arm(
                    commit_state: project_journal._HookCommitState,
                ) -> None:
                    nonlocal verified_state
                    actual_mark_verified(commit_state)
                    verified_state = commit_state

                def interrupt_after_verified() -> None:
                    if verified_state is not None:
                        raise interruption

                args = mock.Mock(repo=str(repo))
                with mock.patch.object(
                    project_journal._HookCommitState,
                    "mark_verified",
                    autospec=True,
                    side_effect=mark_verified_and_arm,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_raise_if_termination_pending",
                        side_effect=interrupt_after_verified,
                    ):
                        with self.assertRaises(
                            project_journal._DeferredTermination
                        ) as raised:
                            project_journal.command_install_hooks(args)

                self.assertIs(raised.exception, interruption)
                self.assertIsNotNone(verified_state)
                self.assertEqual(verified_state.phase, "verified")
                self.assertIsNone(verified_state.pending_step)
                self.assertFalse(verified_state.installed_target_committed)
                notes = "\n".join(getattr(raised.exception, "__notes__", ()))
                self.assertNotIn("incomplete", notes)
                self.assertNotIn("recovery", notes)
                self.assertIn(
                    project_journal.HOOK_BEGIN,
                    (repo / ".githooks/post-merge").read_text(encoding="utf-8"),
                )

    def test_hook_target_fifo_is_rejected_without_blocking(self) -> None:
        repo = self.init_repo()
        fifo = repo / ".git/hooks/post-checkout"
        os.mkfifo(fifo)
        timeout_seconds = 5.0

        started = time.monotonic()
        result = self.run_cli(
            "install-hooks",
            "--repo",
            str(repo),
            timeout_seconds=timeout_seconds,
        )

        self.assertLess(time.monotonic() - started, timeout_seconds)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("not a regular file", result.stderr)

    def test_hook_target_requires_no_follow_primitive(self) -> None:
        repo = self.init_repo().resolve()
        binding = project_journal._bind_hook_directory(
            repo, project_journal._default_hook_path_plan(repo)
        )
        try:
            with mock.patch.object(
                project_journal.os,
                "O_NOFOLLOW",
                0,
            ):
                with self.assertRaisesRegex(
                    project_journal.UnsupportedPlatform,
                    "O_NOFOLLOW",
                ):
                    project_journal._snapshot_hook_target(binding, "post-merge")
        finally:
            project_journal._close_hook_binding(binding)

    def test_hook_installers_are_serialized_by_owner_private_lock(self) -> None:
        repo = self.init_repo().resolve()
        with mock.patch.dict(
            os.environ,
            {"GIT_CONFIG_NOSYSTEM": "1"},
            clear=False,
        ):
            first = project_journal._preflight_hook_targets(repo)
            try:
                with self.assertRaisesRegex(
                    project_journal.UserError,
                    "another project journal hook installation is active",
                ):
                    project_journal._preflight_hook_targets(repo)
            finally:
                project_journal._close_hook_binding(first)

            second = project_journal._preflight_hook_targets(repo)
            project_journal._close_hook_binding(second)

    def test_install_hooks_refuses_intermediate_component_symlink(self) -> None:
        repo = self.init_repo()
        outside = self.root / "outside-hook-tree"
        outside.mkdir()
        (repo / ".hook-parent").symlink_to(outside, target_is_directory=True)
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".hook-parent/hooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)

        result = self.run_cli("install-hooks", "--repo", str(repo))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("descriptor-relative hook path traversal", result.stderr)
        self.assertFalse((outside / "hooks").exists())
        exclude = (repo / ".git/info/exclude").read_text(encoding="utf-8")
        self.assertNotIn("docs/project_journal/INDEX.md", exclude.splitlines())

    def test_install_hooks_detects_racing_intermediate_component_replacement(
        self,
    ) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".hook-parent/hooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        parent = repo / ".hook-parent"
        moved_parent = repo / ".hook-parent-validated-object"
        actual_rename = project_journal._rename_hook_entry_with_flag
        raced = False

        def rename_with_intermediate_race(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal raced
            if destination == "post-merge" and not raced:
                raced = True
                parent.rename(moved_parent)
                (parent / "hooks").mkdir(parents=True)
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_with_intermediate_race,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "ancestor identity or access policy changed",
            ):
                project_journal.command_install_hooks(args)

        self.assertTrue(raced)
        self.assertFalse((parent / "hooks/post-merge").exists())
        self.assertIn(
            project_journal.HOOK_BEGIN,
            (moved_parent / "hooks/post-merge").read_text(encoding="utf-8"),
        )

    def test_install_hooks_detects_racing_parent_replacement(self) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        hooks_dir = repo / ".githooks"
        moved_hooks = repo / ".githooks-validated-object"
        actual_rename = project_journal._rename_hook_entry_with_flag
        raced = False

        def rename_with_parent_race(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal raced
            if destination == "post-merge" and not raced:
                raced = True
                hooks_dir.rename(moved_hooks)
                hooks_dir.mkdir()
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_with_parent_race,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "identity or access policy changed",
            ):
                project_journal.command_install_hooks(args)

        self.assertTrue(raced)
        self.assertFalse((hooks_dir / "post-merge").exists())
        self.assertIn(
            project_journal.HOOK_BEGIN,
            (moved_hooks / "post-merge").read_text(encoding="utf-8"),
        )

    def test_hook_recovery_locator_rebinds_renamed_parent_after_exchange(
        self,
    ) -> None:
        repo = self.init_repo().resolve()
        hooks = repo / ".githooks"
        hooks.mkdir()
        target_name = "post-merge"
        temporary_name = ".project-journal-post-merge-recovery.tmp"
        (hooks / target_name).write_text("displaced hook\n", encoding="utf-8")
        (hooks / temporary_name).write_text("installed hook\n", encoding="utf-8")
        binding = project_journal._bind_hook_directory(
            repo,
            project_journal._HookPathPlan(
                root=repo,
                components=(".githooks",),
            ),
        )
        try:
            displaced, _content = project_journal._snapshot_hook_target(
                binding,
                target_name,
            )
            project_journal._rename_hook_entry_with_flag(
                binding.fd,
                temporary_name,
                target_name,
                exchange=True,
            )
            expected = project_journal.dataclasses.replace(
                displaced,
                name=temporary_name,
            )
            renamed_hooks = repo / ".githooks-renamed"
            hooks.rename(renamed_hooks)
            benign_entry = renamed_hooks / "benign-child-churn"
            benign_entry.write_text("benign\n", encoding="utf-8")
            benign_entry.unlink()

            reference = project_journal._hook_recovery_reference(
                binding,
                temporary_name,
                expected,
            )
            evidence = self.recovery_evidence(reference)
            held, _held_content = project_journal._snapshot_hook_target(
                binding,
                temporary_name,
            )

            self.assertIn("preserved recovery locator", reference)
            self.assertEqual(evidence["path_status"], "path_verified")
            self.assertEqual(evidence["held_object_status"], "verified")
            self.assertEqual(
                evidence["path"],
                str(renamed_hooks / temporary_name),
            )
            self.assertNotIn(str(hooks / temporary_name), reference)
            self.assertEqual(held, expected)
            self.assertEqual(
                evidence["directory"]["inode"],
                binding.identity[1],
            )
            self.assertEqual(
                evidence["leaf_identity"]["inode"],
                expected.identity[1],
            )
        finally:
            project_journal._close_hook_binding(binding)

    def test_hook_recovery_evidence_survives_unresolved_ancestor_replacement(
        self,
    ) -> None:
        repo = self.init_repo().resolve()
        hooks = repo / ".githooks"
        hooks.mkdir()
        target_name = "post-merge"
        temporary_name = ".project-journal-post-merge-recovery.tmp"
        (hooks / target_name).write_text("displaced hook\n", encoding="utf-8")
        (hooks / temporary_name).write_text("installed hook\n", encoding="utf-8")
        binding = project_journal._bind_hook_directory(
            repo,
            project_journal._HookPathPlan(
                root=repo,
                components=(".githooks",),
            ),
        )
        try:
            displaced, _content = project_journal._snapshot_hook_target(
                binding,
                target_name,
            )
            project_journal._rename_hook_entry_with_flag(
                binding.fd,
                temporary_name,
                target_name,
                exchange=True,
            )
            expected = project_journal.dataclasses.replace(
                displaced,
                name=temporary_name,
            )
            relocated_root = repo.parent / "relocated"
            relocated_root.mkdir()
            moved_repo = relocated_root / "repo-moved"
            repo.rename(moved_repo)
            repo.mkdir()
            replacement_hooks = repo / ".githooks"
            replacement_hooks.mkdir()
            (replacement_hooks / temporary_name).write_text(
                "decoy recovery object\n",
                encoding="utf-8",
            )

            reference = project_journal._hook_recovery_reference(
                binding,
                temporary_name,
                expected,
            )
            evidence = self.recovery_evidence(reference)
            held, _held_content = project_journal._snapshot_hook_target(
                binding,
                temporary_name,
            )

            self.assertIn(
                "preserved recovery object with path_unverified",
                reference,
            )
            self.assertEqual(evidence["path_status"], "path_unverified")
            self.assertEqual(evidence["directory_status"], "verified")
            self.assertEqual(evidence["held_object_status"], "verified")
            self.assertNotIn("path", evidence)
            self.assertEqual(held, expected)
            self.assertEqual(
                evidence["directory"]["device"],
                binding.identity[0],
            )
            self.assertEqual(
                evidence["directory"]["inode"],
                binding.identity[1],
            )
            self.assertEqual(
                evidence["leaf_identity"]["device"],
                expected.identity[0],
            )
            self.assertEqual(
                evidence["leaf_identity"]["inode"],
                expected.identity[1],
            )
            self.assertTrue((moved_repo / ".githooks" / temporary_name).exists())
            self.assertNotEqual(
                (replacement_hooks / temporary_name).stat().st_ino,
                expected.identity[1],
            )
        finally:
            project_journal._close_hook_binding(binding)

    def test_install_hooks_detects_racing_allowed_root_replacement(self) -> None:
        repo = self.init_repo()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        first = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(first.returncode, 0, first.stderr)
        moved_repo = self.root / "repo-validated-object"
        actual_rename = project_journal._rename_hook_entry_with_flag
        raced = False

        def rename_with_root_race(
            directory_fd: int,
            source: str,
            destination: str,
            *,
            exchange: bool,
        ) -> None:
            nonlocal raced
            if destination == "post-merge" and not raced:
                raced = True
                repo.rename(moved_repo)
                repo.mkdir()
            actual_rename(
                directory_fd,
                source,
                destination,
                exchange=exchange,
            )

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_with_root_race,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "ancestor identity or access policy changed",
            ):
                project_journal.command_install_hooks(args)

        self.assertTrue(raced)
        self.assertFalse((repo / ".githooks/post-merge").exists())
        self.assertIn(
            project_journal.HOOK_BEGIN,
            (moved_repo / ".githooks/post-merge").read_text(encoding="utf-8"),
        )

    def test_install_hooks_explicitly_rejects_non_posix_platform(self) -> None:
        plan = project_journal._HookPathPlan(
            root=self.root,
            components=(".githooks",),
        )
        with mock.patch.object(project_journal.os, "name", "nt"):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "requires POSIX descriptor-relative filesystem primitives",
            ):
                project_journal._bind_hook_directory(plan.root, plan)

    def test_hook_rename_rejects_other_posix_before_loading_libc(self) -> None:
        with mock.patch.object(project_journal.os, "name", "posix"):
            with mock.patch.object(project_journal.sys, "platform", "freebsd14"):
                with mock.patch.object(project_journal.ctypes, "CDLL") as cdll:
                    with self.assertRaisesRegex(
                        project_journal.UnsupportedPlatform,
                        "supported only on macOS and Linux",
                    ):
                        project_journal._rename_hook_entry_with_flag(
                            -1,
                            "source",
                            "destination",
                            exchange=False,
                        )

        cdll.assert_not_called()

    def test_hook_target_timestamp_only_transition_preserves_identity(self) -> None:
        repo = self.init_repo().resolve()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        installed = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(installed.returncode, 0, installed.stderr)
        binding = project_journal._preflight_hook_targets(repo)
        try:
            target = next(item for item in binding.targets if item.name == "post-merge")
            hook = repo / ".githooks/post-merge"
            hook_stat = hook.stat()
            os.utime(
                hook,
                ns=(hook_stat.st_atime_ns, hook_stat.st_mtime_ns + 1_000_000_000),
            )

            project_journal._revalidate_hook_target(binding, target)
        finally:
            project_journal._close_hook_binding(binding)

    def test_hook_target_revalidation_rejects_object_content_and_access_changes(
        self,
    ) -> None:
        for mutation in ("object", "content", "access"):
            with self.subTest(mutation=mutation):
                repo = self.init_repo(f"repo-target-{mutation}").resolve()
                configured = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                installed = self.run_cli("install-hooks", "--repo", str(repo))
                self.assertEqual(installed.returncode, 0, installed.stderr)
                binding = project_journal._preflight_hook_targets(repo)
                try:
                    target = next(
                        item for item in binding.targets if item.name == "post-merge"
                    )
                    hook = repo / ".githooks/post-merge"
                    if mutation == "object":
                        replacement = repo / ".githooks/replacement"
                        replacement.write_bytes(hook.read_bytes())
                        replacement.chmod(0o755)
                        os.replace(replacement, hook)
                    elif mutation == "content":
                        hook.write_bytes(hook.read_bytes() + b"# changed\n")
                    else:
                        hook.chmod(0o700)

                    with self.assertRaisesRegex(
                        project_journal.UserError,
                        "hook target changed after preflight",
                    ):
                        project_journal._revalidate_hook_target(binding, target)
                finally:
                    project_journal._close_hook_binding(binding)

    def test_hook_directory_revalidation_rejects_group_only_change(self) -> None:
        repo = self.init_repo().resolve()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        installed = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(installed.returncode, 0, installed.stderr)
        binding = project_journal._preflight_hook_targets(repo)
        actual_fstat = os.fstat

        def changed_group(fd: int) -> os.stat_result:
            value = actual_fstat(fd)
            if fd == binding.fd:
                return stat_with_gid(value, value.st_gid + 1)
            return value

        try:
            with mock.patch.object(
                project_journal.os,
                "fstat",
                side_effect=changed_group,
            ):
                with self.assertRaisesRegex(
                    project_journal.UserError,
                    "identity or access policy changed",
                ):
                    project_journal._revalidate_hook_directory(binding)
        finally:
            project_journal._close_hook_binding(binding)

    def test_hook_target_revalidation_rejects_group_only_change(self) -> None:
        repo = self.init_repo().resolve()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        installed = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(installed.returncode, 0, installed.stderr)
        binding = project_journal._preflight_hook_targets(repo)
        target = next(item for item in binding.targets if item.name == "post-merge")
        assert target.identity is not None
        expected_object = target.identity[:2]
        actual_fstat = os.fstat
        actual_stat = os.stat

        def with_changed_group(value: os.stat_result) -> os.stat_result:
            if (value.st_dev, value.st_ino) == expected_object:
                return stat_with_gid(value, value.st_gid + 1)
            return value

        def changed_fstat(fd: int) -> os.stat_result:
            return with_changed_group(actual_fstat(fd))

        def changed_stat(
            path: os.PathLike[str] | str | int,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> os.stat_result:
            return with_changed_group(
                actual_stat(
                    path,
                    dir_fd=dir_fd,
                    follow_symlinks=follow_symlinks,
                )
            )

        try:
            with mock.patch.object(
                project_journal.os,
                "fstat",
                side_effect=changed_fstat,
            ):
                with mock.patch.object(
                    project_journal.os,
                    "stat",
                    side_effect=changed_stat,
                ):
                    with self.assertRaisesRegex(
                        project_journal.UserError,
                        "hook target changed after preflight",
                    ):
                        project_journal._revalidate_hook_target(binding, target)
        finally:
            project_journal._close_hook_binding(binding)

    @unittest.skipUnless(
        sys.platform == "darwin",
        "Darwin extended ACL revalidation contract",
    )
    def test_hook_revalidation_rejects_acl_only_directory_and_target_changes(
        self,
    ) -> None:
        for subject in ("directory", "target"):
            with self.subTest(subject=subject):
                repo = self.init_repo(f"repo-acl-{subject}").resolve()
                configured = run_git(
                    repo,
                    "config",
                    "--local",
                    "core.hooksPath",
                    ".githooks",
                )
                self.assertEqual(configured.returncode, 0, configured.stderr)
                installed = self.run_cli("install-hooks", "--repo", str(repo))
                self.assertEqual(installed.returncode, 0, installed.stderr)
                binding = project_journal._preflight_hook_targets(repo)
                target = next(
                    item for item in binding.targets if item.name == "post-merge"
                )
                changed_path = (
                    repo / ".githooks"
                    if subject == "directory"
                    else repo / ".githooks/post-merge"
                )
                acl = subprocess.run(
                    [
                        "/bin/chmod",
                        "+a",
                        "everyone allow read",
                        str(changed_path),
                    ],
                    check=False,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
                self.assertEqual(acl.returncode, 0, acl.stderr)
                try:
                    with self.assertRaisesRegex(
                        project_journal.UserError,
                        "unsupported extended ACL",
                    ):
                        if subject == "directory":
                            project_journal._revalidate_hook_directory(binding)
                        else:
                            project_journal._revalidate_hook_target(binding, target)
                finally:
                    project_journal._close_hook_binding(binding)

    def test_install_hooks_refuses_symlinked_default_hooks_dir(self) -> None:
        repo = self.init_repo()
        hooks_dir = repo / ".git/hooks"
        shared_hooks = self.root / "shared-hooks"
        shared_hooks.mkdir()
        shutil.rmtree(hooks_dir)
        hooks_dir.symlink_to(shared_hooks, target_is_directory=True)

        result = self.run_cli("install-hooks", "--repo", str(repo))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("descriptor-relative hook path traversal", result.stderr)
        for hook_name in project_journal.HOOK_NAMES:
            self.assertFalse((shared_hooks / hook_name).exists())
        exclude = (repo / ".git/info/exclude").read_text(encoding="utf-8")
        self.assertNotIn("docs/project_journal/INDEX.md", exclude.splitlines())

    def test_install_hooks_refuses_marker_hook_with_extra_content(self) -> None:
        repo = self.init_repo()
        hook = repo / ".git/hooks/post-merge"
        hook.write_text(
            textwrap.dedent(
                f"""\
                #!/bin/sh
                {project_journal.HOOK_BEGIN}
                exit 0
                {project_journal.HOOK_END}
                echo keep-me
                """
            ),
            encoding="utf-8",
        )

        result = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unmanaged content outside", result.stderr)

    def test_install_hooks_respects_core_hooks_path(self) -> None:
        repo = self.init_repo()
        result = run_git(repo, "config", "core.hooksPath", ".githooks")
        self.assertEqual(result.returncode, 0, result.stderr)

        install = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertTrue((repo / ".githooks/post-merge").exists())
        self.assertFalse((repo / ".git/hooks/post-merge").exists())

    def test_install_hooks_preserves_significant_hooks_path_whitespace(self) -> None:
        repo = self.init_repo()
        configured_path = " .githooks "
        result = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            configured_path,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        install = self.run_cli("install-hooks", "--repo", str(repo))

        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertTrue((repo / configured_path / "post-merge").exists())
        self.assertFalse((repo / ".githooks/post-merge").exists())

    def test_hooks_path_parser_removes_only_nul_framing(self) -> None:
        raw_path = b" leading-\xff-trailing \0"

        parsed = project_journal._parse_nul_terminated_git_path(
            raw_path,
            "test core.hooksPath query",
        )

        self.assertEqual(os.fsencode(parsed), raw_path[:-1])
        with self.assertRaisesRegex(
            project_journal.UserError,
            "malformed NUL framing",
        ):
            project_journal._parse_nul_terminated_git_path(
                b"path\0extra\0",
                "test core.hooksPath query",
            )

    def test_repo_hook_path_query_strictly_parses_one_scoped_record(self) -> None:
        output = b"local\0file:/repo/.git/config\0 leading-\xff-trailing \0"

        parsed = project_journal._parse_repo_hook_path_config(
            output,
            "test effective core.hooksPath query",
        )

        self.assertEqual(parsed.scope, "local")
        self.assertEqual(parsed.origin, "file:/repo/.git/config")
        self.assertEqual(
            os.fsencode(parsed.raw_path),
            b" leading-\xff-trailing ",
        )
        for malformed in (
            b"",
            b"local\0file:/repo/.git/config\0path",
            b"local\0file:/repo/.git/config\0path\0extra\0",
            b"global\0file:/repo/.git/config\0path\0",
            b"local\0\0path\0",
            b"local\0file:/repo/.git/config\0path\0"
            b"worktree\0file:/repo/.git/config.worktree\0other\0",
        ):
            with self.subTest(malformed=malformed):
                with self.assertRaises(project_journal.UserError):
                    project_journal._parse_repo_hook_path_config(
                        malformed,
                        "test effective core.hooksPath query",
                    )

    def test_hook_path_plan_uses_one_effective_repo_scope_query(self) -> None:
        repo = self.init_repo().resolve()
        expected_plan = project_journal._HookPathPlan(
            root=repo,
            components=(".githooks",),
        )
        query_output = (
            b"local\0file:" + os.fsencode(repo / ".git/config") + b"\0.githooks\0"
        )

        with mock.patch.object(
            project_journal,
            "_capture_bounded_process",
            return_value=subprocess.CompletedProcess(
                args=[],
                returncode=0,
                stdout=query_output,
                stderr=b"",
            ),
        ) as capture:
            with mock.patch.object(
                project_journal,
                "_hook_path_plan_from_config",
                return_value=expected_plan,
            ) as from_config:
                actual_plan = project_journal._hook_path_plan(repo)

        self.assertEqual(actual_plan, expected_plan)
        capture.assert_called_once()
        argv = capture.call_args.args[0]
        self.assertEqual(
            argv[-8:],
            [
                "config",
                "--includes",
                "--show-scope",
                "--show-origin",
                "--type=path",
                "--null",
                "--get",
                "core.hooksPath",
            ],
        )
        self.assertNotIn(f"core.hooksPath={os.devnull}", argv)
        for safe_value in (
            "core.askPass=",
            f"core.attributesFile={os.devnull}",
            "core.fsmonitor=false",
            "credential.helper=",
            "credential.interactive=never",
        ):
            self.assertIn(safe_value, argv)
        self.assertEqual(
            capture.call_args.kwargs["env"],
            project_journal._git_environment(),
        )
        self.assertEqual(
            capture.call_args.kwargs["verified_runtime"],
            project_journal._require_git_runtime(),
        )
        self.assertEqual(
            from_config.call_args.args[1],
            ".githooks",
        )

    def test_default_hook_plan_requeries_repo_scope_after_safe_preflight(
        self,
    ) -> None:
        repo = self.init_repo().resolve()
        default_plan = project_journal._HookPathPlan(
            root=repo / ".git",
            components=("hooks",),
        )
        events: list[str] = []

        def no_repo_value(*args: object, **kwargs: object) -> object:
            del args, kwargs
            events.append("query")
            return subprocess.CompletedProcess(
                args=[],
                returncode=1,
                stdout=b"",
                stderr=b"",
            )

        def safe_preflight(*args: object, **kwargs: object) -> None:
            del args, kwargs
            events.append("preflight")

        with mock.patch.object(
            project_journal,
            "_capture_bounded_process",
            side_effect=no_repo_value,
        ) as capture:
            with mock.patch.object(
                project_journal,
                "_preflight_global_hooks_config",
                side_effect=safe_preflight,
            ):
                with mock.patch.object(
                    project_journal,
                    "_default_hook_path_plan",
                    return_value=default_plan,
                ):
                    plan = project_journal._hook_path_plan(repo)

        self.assertEqual(plan, default_plan)
        self.assertEqual(events, ["query", "preflight", "query"])
        self.assertEqual(capture.call_count, 2)
        self.assertEqual(
            capture.call_args_list[0].args[0],
            capture.call_args_list[1].args[0],
        )

    def test_install_hooks_applies_git_prefix_path_semantics_before_roots(
        self,
    ) -> None:
        repo = self.init_repo()
        configured_path = "%(prefix)/project-journal-review-hooks"
        result = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            configured_path,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

        install = self.run_cli("install-hooks", "--repo", str(repo))

        self.assertNotEqual(install.returncode, 0)
        self.assertIn("core.hooksPath is outside", install.stderr)
        self.assertFalse((repo / "%(prefix)/project-journal-review-hooks").exists())
        self.assertFalse((repo / ".git/hooks/post-merge").exists())

    def test_install_hooks_respects_included_local_hooks_path(self) -> None:
        repo = self.init_repo()
        included = repo / ".git/hooks.inc"
        included.write_text(
            "[core]\n    hooksPath = .githooks\n",
            encoding="utf-8",
        )
        result = run_git(repo, "config", "include.path", "hooks.inc")
        self.assertEqual(result.returncode, 0, result.stderr)

        install = self.run_cli("install-hooks", "--repo", str(repo))

        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertTrue((repo / ".githooks/post-merge").exists())
        self.assertFalse((repo / ".git/hooks/post-merge").exists())

    def test_install_hooks_respects_worktree_core_hooks_path(self) -> None:
        repo = self.init_repo()
        config_extension = run_git(repo, "config", "extensions.worktreeConfig", "true")
        self.assertEqual(config_extension.returncode, 0, config_extension.stderr)
        hooks_path = run_git(
            repo, "config", "--worktree", "core.hooksPath", ".worktree-hooks"
        )
        self.assertEqual(hooks_path.returncode, 0, hooks_path.stderr)

        install = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(install.returncode, 0, install.stderr)
        self.assertTrue((repo / ".worktree-hooks/post-merge").exists())
        self.assertFalse((repo / ".git/hooks/post-merge").exists())

    def test_install_hooks_refuses_empty_core_hooks_path(self) -> None:
        repo = self.init_repo()
        result = run_git(repo, "config", "core.hooksPath", "")
        self.assertEqual(result.returncode, 0, result.stderr)

        install = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertNotEqual(install.returncode, 0)
        self.assertIn("core.hooksPath is empty", install.stderr)
        self.assertFalse((repo / "post-merge").exists())
        self.assertFalse((repo / ".git/hooks/post-merge").exists())

    def test_install_hooks_refuses_actual_global_hooks_path_until_local_override(
        self,
    ) -> None:
        repo = self.init_repo()
        global_config = self.home / ".gitconfig"
        global_hooks = self.root / "global-hooks"
        global_config.write_text(
            textwrap.dedent(
                f"""\
                [core]
                    hooksPath = {global_hooks}
                """
            ),
            encoding="utf-8",
        )
        actual_git_env = {
            "HOME": str(self.home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "PATH": os.environ.get("PATH", ""),
        }
        selected_git = str(project_journal._fixed_git_executable())
        actual_before = subprocess.run(
            [
                selected_git,
                "-C",
                str(repo),
                "config",
                "--get",
                "core.hooksPath",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=actual_git_env,
        )
        self.assertEqual(actual_before.returncode, 0, actual_before.stderr)
        self.assertEqual(actual_before.stdout.strip(), str(global_hooks))

        refused = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("global core.hooksPath is set", refused.stderr)
        self.assertIn("config --local core.hooksPath .githooks", refused.stderr)
        remediation_text = refused.stderr.split("for example: ", 1)[1].strip()
        remediation_argv = shlex.split(remediation_text)
        remediation_git = pathlib.Path(remediation_argv[0])
        self.assertTrue(
            remediation_git.is_file(),
            "remediation Git path disappeared after helper subprocess exit",
        )
        self.assertNotIn("project-journal-git-runtime-", str(remediation_git))
        remediation_probe = subprocess.run(
            [str(remediation_git), "--version"],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        self.assertEqual(
            remediation_probe.returncode,
            0,
            remediation_probe.stderr,
        )
        self.assertFalse((repo / ".git/hooks/post-merge").exists())
        self.assertFalse((global_hooks / "post-merge").exists())

        local_override = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(local_override.returncode, 0, local_override.stderr)
        installed = self.run_cli("install-hooks", "--repo", str(repo))
        self.assertEqual(installed.returncode, 0, installed.stderr)
        expected_hook = repo / ".githooks/post-merge"
        self.assertTrue(expected_hook.exists())
        actual_hook = subprocess.run(
            [
                selected_git,
                "-C",
                str(repo),
                "rev-parse",
                "--path-format=absolute",
                "--git-path",
                "hooks/post-merge",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=actual_git_env,
        )
        self.assertEqual(actual_hook.returncode, 0, actual_hook.stderr)
        self.assertEqual(
            pathlib.Path(actual_hook.stdout.strip()).resolve(),
            expected_hook.resolve(),
        )

    def test_install_hooks_refuses_unfollowed_global_include(self) -> None:
        repo = self.init_repo()
        included = self.root / "invalid-included-gitconfig"
        included.write_text("[invalid\n", encoding="utf-8")
        (self.home / ".gitconfig").write_text(
            f"[include]\n    path = {included}\n",
            encoding="utf-8",
        )

        result = self.run_cli("install-hooks", "--repo", str(repo))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not follow global includes", result.stderr)
        self.assertIn("config --local core.hooksPath .githooks", result.stderr)
        self.assertFalse((repo / ".git/hooks/post-merge").exists())

    def test_global_config_fifo_is_rejected_without_blocking(self) -> None:
        config = self.root / "global-config-fifo"
        os.mkfifo(config)

        started = time.monotonic()
        with self.assertRaisesRegex(
            project_journal.UserError,
            "not a regular file",
        ):
            project_journal._global_git_config_entries(config)

        self.assertLess(time.monotonic() - started, 1.0)

    def test_global_config_symlink_replacement_is_not_followed(self) -> None:
        config = self.root / "global-config"
        replacement = self.root / "replacement-config"
        config.write_text("[core]\n", encoding="utf-8")
        replacement.write_text("[core]\n    hooksPath = attacker\n", encoding="utf-8")
        actual_open = project_journal.os.open
        replaced = False

        def open_after_replacement(
            path: os.PathLike[str] | str,
            flags: int,
            *args: object,
            **kwargs: object,
        ) -> int:
            nonlocal replaced
            if os.fspath(path) == os.fspath(config) and not replaced:
                replaced = True
                config.unlink()
                config.symlink_to(replacement)
            return actual_open(path, flags, *args, **kwargs)

        with mock.patch.object(
            project_journal.os,
            "open",
            side_effect=open_after_replacement,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "symlink|without following links",
            ):
                project_journal._global_git_config_entries(config)

        self.assertTrue(replaced)
        self.assertEqual(
            replacement.read_text(encoding="utf-8"),
            "[core]\n    hooksPath = attacker\n",
        )

    def test_config_snapshot_requires_nonblocking_no_follow_primitives(self) -> None:
        config = self.root / "global-config"
        config.write_text("[core]\n", encoding="utf-8")

        for name in ("O_NOFOLLOW", "O_NONBLOCK"):
            with self.subTest(name=name):
                with mock.patch.object(project_journal.os, name, 0):
                    with self.assertRaisesRegex(
                        project_journal.UnsupportedPlatform,
                        name,
                    ):
                        project_journal._global_git_config_entries(config)

    def test_config_snapshot_allows_timestamp_only_transition(self) -> None:
        config = self.root / "global-config"
        content = b"[core]\n"
        config.write_bytes(content)
        original = config.stat()
        actual_read = project_journal.os.read
        changed = False

        def read_after_timestamp_change(fd: int, size: int) -> bytes:
            nonlocal changed
            chunk = actual_read(fd, size)
            if chunk and not changed:
                changed = True
                os.utime(
                    config,
                    ns=(
                        original.st_atime_ns,
                        original.st_mtime_ns + 1_000_000_000,
                    ),
                )
            return chunk

        with mock.patch.object(
            project_journal.os,
            "read",
            side_effect=read_after_timestamp_change,
        ):
            snapshot = project_journal._secure_read_regular_path(
                config,
                label="test config",
                byte_limit=1024,
            )

        self.assertTrue(changed)
        self.assertEqual(snapshot, content)

    def test_secure_read_preserves_primary_error_over_descriptor_close_failure(
        self,
    ) -> None:
        config = self.root / "global-config-primary-close"
        config.write_text("[core]\n", encoding="utf-8")
        actual_close = project_journal.os.close
        primary = LegacyInterrupt("injected secure-read primary")
        close_error = OSError(errno.EIO, "injected secure-read close failure")
        close_failed = False
        raised_error: LegacyInterrupt | None = None
        traceback_names: list[str] = []

        def fail_read(_fd: int) -> os.stat_result:
            raise primary

        def close_then_fail(fd: int) -> None:
            nonlocal close_failed
            actual_close(fd)
            if not close_failed:
                close_failed = True
                raise close_error

        with mock.patch.object(
            project_journal.os,
            "fstat",
            side_effect=fail_read,
        ):
            with mock.patch.object(
                project_journal.os,
                "close",
                side_effect=close_then_fail,
            ):
                try:
                    project_journal._secure_read_regular_path(
                        config,
                        label="test config",
                        byte_limit=1024,
                    )
                except LegacyInterrupt as raised:
                    raised_error = raised
                    traceback_names = self.exception_traceback_names(raised)
                else:
                    self.fail("expected secure-read primary error")

        self.assertIs(raised_error, primary)
        self.assertTrue(close_failed)
        notes = "\n".join(getattr(primary, "__notes__", ()))
        self.assertIn("test config descriptor cleanup failed", notes)
        self.assertIn("type=OSError", notes)
        self.assertIn("errno=5 (EIO)", notes)
        self.assertIn("injected secure-read close failure", notes)
        self.assertIn("fail_read", traceback_names)

    def test_secure_read_wraps_descriptor_close_only_failure(self) -> None:
        config = self.root / "global-config-close-only"
        content = b"[core]\n"
        config.write_bytes(content)
        actual_close = project_journal.os.close
        close_error = OSError(errno.EIO, "injected close-only failure")

        def close_then_fail(fd: int) -> None:
            actual_close(fd)
            raise close_error

        with mock.patch.object(
            project_journal.os,
            "close",
            side_effect=close_then_fail,
        ):
            with self.assertRaises(project_journal.UserError) as raised:
                project_journal._secure_read_regular_path(
                    config,
                    label="test config",
                    byte_limit=1024,
                )

        self.assertIs(raised.exception.__cause__, close_error)
        self.assertIn(
            "test config descriptor cleanup failed",
            str(raised.exception),
        )
        self.assertIn("type=OSError", str(raised.exception))
        self.assertIn("errno=5 (EIO)", str(raised.exception))

    def test_secure_read_does_not_consume_ambient_exception(self) -> None:
        config = self.root / "global-config-ambient-close"
        config.write_text("[core]\n", encoding="utf-8")
        ambient = RuntimeError("unrelated outer exception")
        close_error = OSError(
            errno.EIO,
            "injected secure-read ambient close failure",
        )
        actual_close = project_journal.os.close

        def close_then_fail(fd: int) -> None:
            actual_close(fd)
            raise close_error

        try:
            raise ambient
        except RuntimeError:
            with (
                mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=close_then_fail,
                ),
                self.assertRaises(project_journal.UserError) as raised,
            ):
                project_journal._secure_read_regular_path(
                    config,
                    label="test config",
                    byte_limit=1024,
                )

        self.assertIs(raised.exception.__cause__, close_error)
        self.assertIn(
            "test config descriptor cleanup failed",
            str(raised.exception),
        )
        self.assertEqual(getattr(ambient, "__notes__", ()), ())

    def test_install_hooks_refuses_actual_system_hooks_path_until_local_override(
        self,
    ) -> None:
        repo = self.init_repo()
        system_config = self.root / "system-gitconfig"
        system_hooks = self.root / "system-hooks"
        system_config.write_text(
            f"[core]\n    hooksPath = {system_hooks}\n",
            encoding="utf-8",
        )
        system_env = {
            "GIT_CONFIG_NOSYSTEM": "0",
            "GIT_CONFIG_SYSTEM": str(system_config),
            "GIT_CONFIG_GLOBAL": os.devnull,
        }

        refused = self.run_cli(
            "install-hooks",
            "--repo",
            str(repo),
            env=system_env,
        )

        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("system core.hooksPath is set", refused.stderr)
        self.assertIn("config --local core.hooksPath .githooks", refused.stderr)
        self.assertFalse((repo / ".git/hooks/post-merge").exists())
        self.assertFalse((system_hooks / "post-merge").exists())

        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        installed = self.run_cli(
            "install-hooks",
            "--repo",
            str(repo),
            env=system_env,
        )
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertTrue((repo / ".githooks/post-merge").exists())

    def test_install_hooks_fails_closed_for_implicit_system_config(self) -> None:
        repo = self.init_repo()

        result = self.run_cli(
            "install-hooks",
            "--repo",
            str(repo),
            env={"GIT_CONFIG_NOSYSTEM": "0"},
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "implicit system Git config path cannot be proved safely",
            result.stderr,
        )
        self.assertIn("GIT_CONFIG_SYSTEM", result.stderr)
        self.assertIn("GIT_CONFIG_NOSYSTEM=1", result.stderr)
        self.assertFalse((repo / ".git/hooks/post-merge").exists())

    def test_system_config_requires_raw_absolute_path(self) -> None:
        for configured in ("relative-gitconfig", "~/gitconfig", ""):
            with self.subTest(configured=configured):
                with mock.patch.dict(
                    os.environ,
                    {
                        "GIT_CONFIG_NOSYSTEM": "0",
                        "GIT_CONFIG_SYSTEM": configured,
                    },
                    clear=False,
                ):
                    with self.assertRaises(project_journal.UserError):
                        project_journal._system_git_config_entries()

    def test_explicit_system_config_fifo_and_symlink_are_not_followed(self) -> None:
        regular = self.root / "system-config-regular"
        regular.write_text("[core]\n", encoding="utf-8")
        fifo = self.root / "system-config-fifo"
        os.mkfifo(fifo)
        symlink = self.root / "system-config-symlink"
        symlink.symlink_to(regular)

        for name, path, expected in (
            ("fifo", fifo, "not a regular file"),
            ("symlink", symlink, "symlink|without following links"),
        ):
            with self.subTest(name=name):
                with mock.patch.dict(
                    os.environ,
                    {
                        "GIT_CONFIG_NOSYSTEM": "0",
                        "GIT_CONFIG_SYSTEM": str(path),
                    },
                    clear=False,
                ):
                    started = time.monotonic()
                    with self.assertRaisesRegex(
                        project_journal.UserError,
                        expected,
                    ):
                        project_journal._system_git_config_entries()
                    self.assertLess(time.monotonic() - started, 1.0)

    def test_install_hooks_refuses_unfollowed_system_include(self) -> None:
        repo = self.init_repo()
        system_config = self.root / "system-gitconfig"
        unrelated = self.root / "must-not-be-read"
        unrelated.write_text("[invalid\n", encoding="utf-8")
        system_config.write_text(
            f"[include]\n    path = {unrelated}\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "install-hooks",
            "--repo",
            str(repo),
            env={
                "GIT_CONFIG_NOSYSTEM": "0",
                "GIT_CONFIG_SYSTEM": str(system_config),
                "GIT_CONFIG_GLOBAL": os.devnull,
            },
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not follow system includes", result.stderr)
        self.assertIn("config --local core.hooksPath .githooks", result.stderr)
        self.assertFalse((repo / ".git/hooks/post-merge").exists())

    def test_install_hooks_accepts_explicitly_disabled_global_config(self) -> None:
        repo = self.init_repo()

        result = self.run_cli(
            "install-hooks",
            "--repo",
            str(repo),
            env={"GIT_CONFIG_GLOBAL": os.devnull},
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((repo / ".git/hooks/post-merge").exists())

    def test_install_hooks_refuses_configured_hooks_path_outside_repo(self) -> None:
        for name, hooks_path in (
            ("absolute", str(self.root / "shared-hooks")),
            ("relative", "../shared-hooks"),
        ):
            with self.subTest(name=name):
                repo = self.init_repo(f"repo-{name}")
                result = run_git(repo, "config", "core.hooksPath", hooks_path)
                self.assertEqual(result.returncode, 0, result.stderr)

                install = self.run_cli("install-hooks", "--repo", str(repo))
                self.assertNotEqual(install.returncode, 0)
                self.assertIn("outside the repository", install.stderr)
                self.assertFalse((self.root / "shared-hooks/post-merge").exists())
                self.assertFalse((repo / ".git/hooks/post-merge").exists())

    def test_path_date_uses_explicit_sessions_root(self) -> None:
        path = pathlib.Path(
            "/tmp/sessions/.codex/sessions/2026/05/05/rollout-test.jsonl"
        )
        rollout_root = pathlib.Path("/tmp/sessions/.codex/sessions")

        dated = project_journal._path_date(path, rollout_root)

        self.assertIsNotNone(dated)
        self.assertEqual(dated.isoformat(), "2026-05-05")

    def test_path_date_reads_flat_archived_rollout_name(self) -> None:
        path = pathlib.Path(
            "/tmp/.codex/archived_sessions/rollout-2026-05-06T12-34-56-abcdef.jsonl"
        )
        rollout_root = pathlib.Path("/tmp/.codex/archived_sessions")

        dated = project_journal._path_date(path, rollout_root)

        self.assertIsNotNone(dated)
        self.assertEqual(dated.isoformat(), "2026-05-06")

    def test_path_date_rejects_nested_archived_rollout_name(self) -> None:
        rollout_root = pathlib.Path("/tmp/.codex/archived_sessions")
        path = rollout_root / "backup" / "rollout-2026-05-06T12-34-56-abcdef.jsonl"

        self.assertIsNone(project_journal._path_date(path, rollout_root))

    def test_rollout_directory_cleanup_attempts_every_bound_resource(self) -> None:
        first_entries = mock.Mock()
        second_entries = mock.Mock()
        second_entries.close.side_effect = OSError(
            errno.EIO,
            "injected iterator close failure",
        )
        frames = [
            project_journal._RolloutDirectoryFrame(
                binding=project_journal._RolloutDirectoryBinding(
                    rollout_root=pathlib.Path("/sessions"),
                    path=pathlib.Path("/sessions/first"),
                    fd=101,
                    object_identity=(1, 1),
                    access_policy=(1, 1, 0o700),
                    parent_fd=None,
                    name=None,
                ),
                entries=first_entries,
            ),
            project_journal._RolloutDirectoryFrame(
                binding=project_journal._RolloutDirectoryBinding(
                    rollout_root=pathlib.Path("/sessions"),
                    path=pathlib.Path("/sessions/second"),
                    fd=202,
                    object_identity=(2, 2),
                    access_policy=(2, 2, 0o700),
                    parent_fd=101,
                    name="second",
                ),
                entries=second_entries,
            ),
        ]

        with mock.patch.object(project_journal.os, "close") as close:
            with self.assertRaisesRegex(OSError, "iterator close failure"):
                project_journal._close_rollout_directory_frames(frames)

        self.assertEqual(frames, [])
        first_entries.close.assert_called_once_with()
        second_entries.close.assert_called_once_with()
        self.assertEqual(
            close.call_args_list,
            [mock.call(202), mock.call(101)],
        )

    def test_rollout_directory_frame_cleanup_is_secondary_to_inspection_failure(
        self,
    ) -> None:
        entries = mock.Mock()
        entries.close.side_effect = OSError(
            errno.EIO,
            "injected iterator close failure",
        )
        frame = project_journal._RolloutDirectoryFrame(
            binding=project_journal._RolloutDirectoryBinding(
                rollout_root=pathlib.Path("/sessions"),
                path=pathlib.Path("/sessions/2099"),
                fd=303,
                object_identity=(3, 3),
                access_policy=(3, 3, 0o700),
                parent_fd=None,
                name=None,
            ),
            entries=entries,
        )
        failure = project_journal._rollout_inspection_failure(
            frame.binding.path,
            rollout_root=frame.binding.rollout_root,
            inspection_reason="directory_replaced",
            detail="injected primary replacement",
        )

        with mock.patch.object(
            project_journal.os,
            "close",
            side_effect=OSError(
                errno.EBADF,
                "injected descriptor close failure",
            ),
        ):
            project_journal._close_rollout_directory_frame_preserving_failure(
                frame,
                failure,
                context="injected frame cleanup",
            )

        self.assertEqual(failure.error.inspection_reason, "directory_replaced")
        self.assertEqual(len(failure.error.cleanup_errors), 1)
        cleanup = failure.error.cleanup_errors[0]
        self.assertEqual(cleanup["context"], "injected frame cleanup")
        self.assertEqual(cleanup["errno"], errno.EIO)
        self.assertIn("iterator close failure", cleanup["message"])
        self.assertIn("descriptor cleanup also failed", cleanup["details"][0])
        self.assertTrue(
            any(
                "injected frame cleanup" in note
                for note in getattr(failure.error, "__notes__", ())
            )
        )

    def test_rollout_directory_binding_cleanup_preserves_structured_failure(
        self,
    ) -> None:
        root = self.root / "sessions"
        expected_stat = self.root.stat()
        state = project_journal._DiscoveryScanState(
            deadline=time.monotonic() + 5,
        )
        parent = project_journal._RolloutDirectoryBinding(
            rollout_root=root,
            path=root,
            fd=404,
            object_identity=(4, 4),
            access_policy=(4, 4, 0o700),
            parent_fd=None,
            name=None,
        )

        for binding_kind in ("root", "child"):
            with self.subTest(binding_kind=binding_kind):
                path = root if binding_kind == "root" else root / "2099"
                failure = project_journal._rollout_inspection_failure(
                    path,
                    rollout_root=root,
                    inspection_reason="directory_access_policy_changed",
                    detail="injected primary policy change",
                )
                with mock.patch.object(
                    project_journal.os,
                    "stat",
                    return_value=expected_stat,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "open",
                        return_value=505,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_validate_opened_rollout_directory",
                            return_value=failure,
                        ):
                            with mock.patch.object(
                                project_journal.os,
                                "close",
                                side_effect=OSError(
                                    errno.EIO,
                                    "injected binding close failure",
                                ),
                            ):
                                if binding_kind == "root":
                                    result = (
                                        project_journal._bind_rollout_root_directory(
                                            root,
                                            state,
                                        )
                                    )
                                else:
                                    result = (
                                        project_journal._bind_rollout_child_directory(
                                            parent,
                                            "2099",
                                            path,
                                            expected_stat,
                                            state,
                                        )
                                    )

                self.assertIs(result, failure)
                self.assertEqual(
                    result.error.inspection_reason,
                    "directory_access_policy_changed",
                )
                self.assertEqual(len(result.error.cleanup_errors), 1)
                cleanup = result.error.cleanup_errors[0]
                self.assertIn(binding_kind, cleanup["context"])
                self.assertEqual(cleanup["errno"], errno.EIO)
                self.assertIn("binding close failure", cleanup["message"])

    def test_preferred_ancestor_inherits_superseded_binding_cleanup_error(
        self,
    ) -> None:
        root = pathlib.Path("/sessions")
        child_failure = project_journal._rollout_inspection_failure(
            root / "2099",
            rollout_root=root,
            inspection_reason="directory_scan_failed",
            detail="injected child scan failure",
        )
        ancestor_failure = project_journal._rollout_inspection_failure(
            root,
            rollout_root=root,
            inspection_reason="directory_replaced",
            detail="injected preferred ancestor replacement",
        )
        project_journal._record_rollout_cleanup_error(
            child_failure.error,
            context="injected child binding cleanup",
            cleanup_error=OSError(
                errno.EIO,
                "injected child binding close failure",
            ),
        )

        project_journal._inherit_rollout_cleanup_errors(
            ancestor_failure,
            child_failure,
        )

        self.assertEqual(
            ancestor_failure.error.inspection_reason,
            "directory_replaced",
        )
        self.assertEqual(len(ancestor_failure.error.cleanup_errors), 1)
        cleanup = ancestor_failure.error.cleanup_errors[0]
        self.assertEqual(cleanup["errno"], errno.EIO)
        self.assertIn("child binding close failure", cleanup["message"])
        self.assertTrue(
            any(
                "child binding cleanup" in note
                for note in getattr(ancestor_failure.error, "__notes__", ())
            )
        )

    def test_unframed_rollout_cleanup_preserves_revalidation_failure(
        self,
    ) -> None:
        root = pathlib.Path("/sessions")
        binding = project_journal._RolloutDirectoryBinding(
            rollout_root=root,
            path=root / "2099",
            fd=606,
            object_identity=(6, 6),
            access_policy=(6, 6, 0o700),
            parent_fd=None,
            name=None,
        )
        state = project_journal._DiscoveryScanState(
            deadline=time.monotonic() + 5,
        )
        scan_failure = project_journal._rollout_inspection_failure(
            binding.path,
            rollout_root=root,
            inspection_reason="directory_scan_failed",
            detail="injected scan failure",
        )
        policy_failure = project_journal._rollout_inspection_failure(
            root,
            rollout_root=root,
            inspection_reason="directory_access_policy_changed",
            detail="injected primary policy change",
        )

        with mock.patch.object(
            project_journal,
            "_revalidate_rollout_directory_chain",
            return_value=policy_failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "close",
                side_effect=OSError(
                    errno.EIO,
                    "injected unframed close failure",
                ),
            ):
                result = (
                    project_journal._revalidate_and_close_unframed_rollout_directory(
                        [],
                        binding,
                        state,
                        fallback_failure=scan_failure,
                    )
                )

        self.assertIs(result, policy_failure)
        self.assertEqual(scan_failure.error.cleanup_errors, [])
        coverage = project_journal._discovery_coverage_error(
            result.error,
            state,
            source=result.path,
        )
        self.assertEqual(
            coverage["inspection_reason"],
            "directory_access_policy_changed",
        )
        self.assertEqual(len(coverage["cleanup_errors"]), 1)
        cleanup = coverage["cleanup_errors"][0]
        self.assertEqual(cleanup["errno"], errno.EIO)
        self.assertIn("unframed close failure", cleanup["message"])

    def test_rollout_descriptor_cleanup_is_secondary_to_discovery_errors(
        self,
    ) -> None:
        path = pathlib.Path("/sessions/rollout-error.jsonl")
        state = project_journal._DiscoveryScanState(
            deadline=time.monotonic() + 5,
        )
        errors = (
            project_journal.DiscoveryRolloutParseError(
                parse_reason="invalid_json",
                record_number=1,
                byte_offset=0,
                detail="injected parse failure",
            ),
            project_journal.DiscoveryLimitExceeded(
                "rollout line bytes",
                10,
                11,
            ),
            project_journal.DiscoveryRolloutInspectionError(
                inspection_reason="access_policy_changed",
                path=path,
                detail="injected policy failure",
            ),
        )

        for primary in errors:
            with self.subTest(primary=type(primary).__name__):
                original_args = primary.args
                with mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=OSError(
                        errno.EIO,
                        "injected rollout descriptor close failure",
                    ),
                ):
                    project_journal._close_rollout_descriptor_preserving_error(
                        707,
                        primary,
                        context="injected rollout descriptor cleanup",
                    )

                self.assertEqual(primary.args, original_args)
                coverage = project_journal._discovery_coverage_error(
                    primary,
                    state,
                    source=path,
                )
                self.assertEqual(len(coverage["cleanup_errors"]), 1)
                cleanup = coverage["cleanup_errors"][0]
                self.assertEqual(cleanup["errno"], errno.EIO)
                self.assertIn("descriptor close failure", cleanup["message"])

    def test_partial_discovery_coverage_uses_one_deterministic_primary_anchor(
        self,
    ) -> None:
        errors = [
            {
                "code": "discovery_rollout_parse_failed",
                "message": f"unique-error-{index}-" + "x" * 256,
                "source": f"/sessions/rollout-{index}.jsonl",
            }
            for index in range(project_journal.MAX_DISCOVERY_ERRORS)
        ]
        rows = [
            {
                "repo": "/repo-z",
                "candidate_cwd": None,
                "discovery_error": None,
            },
            {
                "repo": None,
                "candidate_cwd": "/repo-m",
                "discovery_error": {
                    "repo_resolution": {
                        "code": "repository_resolution_failed",
                        "message": "row-local failure",
                    }
                },
            },
            {
                "repo": "/repo-a",
                "candidate_cwd": None,
                "discovery_error": None,
            },
        ]

        def marked(input_rows: list[dict[str, object]]) -> list[dict[str, object]]:
            state = project_journal._DiscoveryScanState(
                deadline=time.monotonic() + 5,
            )
            cloned = json.loads(json.dumps(input_rows))
            return project_journal._mark_partial_discovery_coverage(
                cloned,
                json.loads(json.dumps(errors)),
                len(errors),
                state,
            )

        forward = marked(rows)
        reverse = marked(list(reversed(rows)))
        forward_json = json.dumps(
            forward,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        reverse_json = json.dumps(
            reverse,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

        self.assertEqual(forward_json, reverse_json)
        self.assertEqual(
            [row["repo"] or row["candidate_cwd"] for row in forward],
            ["/repo-a", "/repo-m", "/repo-z"],
        )
        primaries = [
            row["discovery_error"]["discovery_coverage"]
            for row in forward
            if isinstance(row.get("discovery_error"), dict)
            and "discovery_coverage" in row["discovery_error"]
        ]
        self.assertEqual(len(primaries), 1)
        primary = primaries[0]
        coverage_id = primary["coverage_id"]
        self.assertEqual(primary["errors"], errors)
        self.assertEqual(forward_json.count('"errors"'), 1)
        self.assertEqual(forward_json.count("unique-error-31-"), 1)

        anchors = {
            row["discovery_error"]["discovery_coverage"]["coverage_id"]: row
            for row in forward
            if isinstance(row.get("discovery_error"), dict)
            and "discovery_coverage" in row["discovery_error"]
        }
        for row in forward:
            self.assertEqual(
                row["discovery_coverage_ref"],
                {"coverage_id": coverage_id},
            )
            self.assertIsNotNone(anchors[coverage_id]["repo"])
            self.assertEqual(row["coverage_status"], "partial")
            self.assertEqual(row["discovery_status"], "inconclusive")

        unresolved = next(row for row in forward if row["candidate_cwd"] == "/repo-m")
        self.assertIn("repo_resolution", unresolved["discovery_error"])
        self.assertNotIn("discovery_coverage", unresolved["discovery_error"])
        naive_repeated_error_bytes = len(forward) * len(
            json.dumps(errors, separators=(",", ":"))
        )
        self.assertLess(len(forward_json), naive_repeated_error_bytes)

        many_rows = [
            {
                "repo": f"/repo-{index:03d}",
                "candidate_cwd": None,
                "discovery_error": None,
            }
            for index in range(64)
        ]
        many_json = json.dumps(
            marked(list(reversed(many_rows))),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        retained_error_bytes = len(json.dumps(errors, separators=(",", ":")))
        self.assertEqual(many_json.count('"errors"'), 1)
        self.assertEqual(many_json.count("unique-error-31-"), 1)
        self.assertLess(
            len(many_json),
            retained_error_bytes * 2 + len(many_rows) * 1024,
        )

    def test_partial_discovery_coverage_sentinel_keeps_primary_and_reference(
        self,
    ) -> None:
        state = project_journal._DiscoveryScanState(
            deadline=time.monotonic() + 5,
        )
        errors = [
            {
                "code": "discovery_limit_exceeded",
                "message": "injected coverage limit",
                "source": "/sessions",
            }
        ]

        rows = project_journal._mark_partial_discovery_coverage(
            [],
            errors,
            1,
            state,
        )

        self.assertEqual(len(rows), 1)
        primary = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            rows[0]["discovery_coverage_ref"],
            {"coverage_id": primary["coverage_id"]},
        )
        self.assertEqual(primary["errors"], errors)

    def test_open_rollout_candidate_preserves_policy_failure_when_close_fails(
        self,
    ) -> None:
        path = self.root / "rollout-policy.jsonl"
        path.write_text("{}\n", encoding="utf-8")
        path_stat = path.stat()
        candidate = project_journal._rollout_candidate_from_stat(
            path,
            path_stat,
            rollout_root=self.root,
        )
        descriptor_stat = stat_with_gid(path_stat, path_stat.st_gid + 1)
        state = project_journal._DiscoveryScanState(
            deadline=time.monotonic() + 5,
        )

        with mock.patch.object(project_journal.os, "open", return_value=808):
            with mock.patch.object(
                project_journal.os,
                "fstat",
                return_value=descriptor_stat,
            ):
                with mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=OSError(
                        errno.EIO,
                        "injected candidate close failure",
                    ),
                ):
                    with self.assertRaises(
                        project_journal.DiscoveryRolloutInspectionError,
                    ) as raised:
                        project_journal._open_rollout_candidate(
                            candidate,
                            state,
                        )

        self.assertEqual(
            raised.exception.inspection_reason,
            "access_policy_changed",
        )
        self.assertEqual(len(raised.exception.cleanup_errors), 1)
        cleanup = raised.exception.cleanup_errors[0]
        self.assertEqual(cleanup["errno"], errno.EIO)
        self.assertIn("candidate close failure", cleanup["message"])

    def test_rollout_parse_failure_survives_descriptor_close_failure(self) -> None:
        path = self.root / "rollout-invalid-json.jsonl"
        path.write_bytes(b"{invalid-json}\n")
        path_stat = path.stat()
        candidate = project_journal._rollout_candidate_from_stat(
            path,
            path_stat,
            rollout_root=self.root,
        )
        state = project_journal._DiscoveryScanState(
            deadline=time.monotonic() + 5,
        )
        actual_close = project_journal.os.close
        close_failed = False

        def close_then_fail(fd: int) -> None:
            nonlocal close_failed
            actual_close(fd)
            close_failed = True
            raise OSError(
                errno.EIO,
                "injected extraction close failure",
            )

        with mock.patch.object(
            project_journal.os,
            "close",
            side_effect=close_then_fail,
        ):
            with self.assertRaises(
                project_journal.DiscoveryRolloutParseError,
            ) as raised:
                list(project_journal._extract_cwds(candidate, state))

        self.assertTrue(close_failed)
        self.assertEqual(raised.exception.parse_reason, "invalid_json")
        coverage = project_journal._discovery_coverage_error(
            raised.exception,
            state,
            source=path,
        )
        self.assertEqual(coverage["parse_reason"], "invalid_json")
        self.assertEqual(len(coverage["cleanup_errors"]), 1)
        cleanup = coverage["cleanup_errors"][0]
        self.assertEqual(cleanup["errno"], errno.EIO)
        self.assertIn("extraction close failure", cleanup["message"])

    def test_rollout_generator_cleanup_preserves_generator_exit(self) -> None:
        path = self.root / "rollout-generator-close.jsonl"
        path.write_text(
            json.dumps({"payload": {"cwd": str(self.root)}}) + "\n",
            encoding="utf-8",
        )
        candidate = project_journal._rollout_candidate_from_stat(
            path,
            path.stat(),
            rollout_root=self.root,
        )
        state = project_journal._DiscoveryScanState(
            deadline=time.monotonic() + 5,
        )
        actual_close = project_journal.os.close

        def close_then_fail(fd: int) -> None:
            actual_close(fd)
            raise OSError(
                errno.EIO,
                "injected generator cleanup close failure",
            )

        generator = project_journal._extract_cwds(candidate, state)
        self.assertEqual(next(generator), str(self.root))
        with mock.patch.object(
            project_journal.os,
            "close",
            side_effect=close_then_fail,
        ):
            with mock.patch.object(
                project_journal,
                "_record_rollout_cleanup_error",
                wraps=project_journal._record_rollout_cleanup_error,
            ) as recorder:
                generator.close()

        self.assertEqual(recorder.call_count, 1)
        primary = recorder.call_args.args[0]
        self.assertIsInstance(primary, GeneratorExit)
        self.assertEqual(len(primary.cleanup_errors), 1)
        cleanup = primary.cleanup_errors[0]
        self.assertEqual(cleanup["errno"], errno.EIO)
        self.assertIn("generator cleanup close failure", cleanup["message"])

    def test_discover_repos_reads_archive_without_active_sessions(self) -> None:
        repo = self.init_repo()
        codex_home = self.root / "codex-home"
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2026-05-06T12-34-56-archive-only.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )

        rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 1)
        self.assertTrue(os.path.samefile(rows[0]["repo"], repo))
        self.assertEqual(rows[0]["rollout_count"], 1)
        self.assertEqual(rows[0]["coverage_status"], "complete")

    def test_discover_repos_ignores_nested_archive_rollouts(self) -> None:
        direct_repo = self.init_repo("direct-archive-repo")
        nested_repo = self.init_repo("nested-archive-repo")
        codex_home = self.root / "codex-home-flat-archive"
        archive = codex_home / "archived_sessions"
        nested_archive = archive / "backup"
        nested_archive.mkdir(parents=True)
        (archive / "rollout-2026-05-06T12-34-56-direct.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(direct_repo)}}) + "\n",
            encoding="utf-8",
        )
        (nested_archive / "rollout-2026-05-07T12-34-56-nested.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(nested_repo)}}) + "\n",
            encoding="utf-8",
        )
        (nested_archive / "rollout-2026-05-08T12-34-56-broken.jsonl").write_bytes(
            b'{"payload":\n'
        )

        rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 1)
        self.assertTrue(os.path.samefile(rows[0]["repo"], direct_repo))
        self.assertEqual(rows[0]["rollout_count"], 1)
        self.assertEqual(rows[0]["coverage_status"], "complete")

    def test_discover_repos_does_not_inspect_nested_archive_directories(
        self,
    ) -> None:
        repo = self.init_repo("direct-archive-unreadable-neighbor")
        codex_home = self.root / "codex-home-flat-unreadable-archive"
        archive = codex_home / "archived_sessions"
        nested_archive = archive / "unreadable-backup"
        nested_archive.mkdir(parents=True)
        direct_rollout = archive / "rollout-2026-05-06T12-34-56-direct.jsonl"
        direct_rollout.write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        actual_scandir = project_journal.os.scandir
        with actual_scandir(archive) as entries:
            direct_entry = next(
                entry for entry in entries if pathlib.Path(entry.path) == direct_rollout
            )
        nested_entry = mock.Mock()
        nested_entry.name = nested_archive.name
        nested_entry.path = str(nested_archive)
        nested_entry.stat.side_effect = AssertionError(
            "flat archive discovery must not inspect child directories"
        )
        archive_scandir = mock.MagicMock()
        archive_scandir.__next__.side_effect = [
            direct_entry,
            nested_entry,
            StopIteration,
        ]
        archive_identity = project_journal._rollout_object_identity(archive.stat())

        def inject_flat_archive(
            path: int | os.PathLike[str] | str,
        ) -> object:
            if isinstance(path, int):
                identity = project_journal._rollout_object_identity(os.fstat(path))
                if identity == archive_identity:
                    return archive_scandir
            return actual_scandir(path)

        with mock.patch.object(
            project_journal.os,
            "scandir",
            side_effect=inject_flat_archive,
        ):
            with mock.patch.object(
                project_journal,
                "_bind_rollout_child_directory",
                side_effect=AssertionError(
                    "flat archive discovery must not bind child directories"
                ),
            ) as bind_child:
                rows = project_journal._discover_repos(codex_home, 9999)

        bind_child.assert_not_called()
        nested_entry.stat.assert_not_called()
        self.assertEqual(len(rows), 1)
        self.assertTrue(os.path.samefile(rows[0]["repo"], repo))
        self.assertEqual(rows[0]["rollout_count"], 1)
        self.assertEqual(rows[0]["coverage_status"], "complete")

    def test_discover_repos_aggregates_and_deduplicates_active_archive(
        self,
    ) -> None:
        repo = self.init_repo()
        codex_home = self.root / "codex-home"
        active = codex_home / "sessions/2026/05/05"
        archive = codex_home / "archived_sessions"
        active.mkdir(parents=True)
        archive.mkdir(parents=True)
        duplicate_name = "rollout-2026-05-05T10-00-00-duplicate.jsonl"
        payload = json.dumps({"payload": {"cwd": str(repo)}}) + "\n"
        (active / duplicate_name).write_text(payload, encoding="utf-8")
        (archive / duplicate_name).write_text(payload, encoding="utf-8")
        (archive / "rollout-2026-05-06T10-00-00-distinct.jsonl").write_text(
            payload,
            encoding="utf-8",
        )

        with mock.patch.object(
            project_journal,
            "MAX_DISCOVERY_ROLLOUT_ASSOCIATIONS",
            2,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 1)
        self.assertTrue(os.path.samefile(rows[0]["repo"], repo))
        self.assertEqual(rows[0]["rollout_count"], 2)
        self.assertEqual(rows[0]["coverage_status"], "complete")

    def test_discover_repos_merges_conflicting_duplicate_rollout_cwds_once(
        self,
    ) -> None:
        first_repo = self.init_repo("first-repo")
        second_repo = self.init_repo("second-repo")
        codex_home = self.root / "codex-home"
        active = codex_home / "sessions/2026/05/05"
        archive = codex_home / "archived_sessions"
        active.mkdir(parents=True)
        archive.mkdir(parents=True)
        duplicate_name = "rollout-2026-05-05T10-00-00-duplicate.jsonl"
        (active / duplicate_name).write_text(
            json.dumps({"payload": {"cwd": str(first_repo)}}) + "\n",
            encoding="utf-8",
        )
        (archive / duplicate_name).write_text(
            json.dumps({"payload": {"cwd": str(second_repo)}}) + "\n",
            encoding="utf-8",
        )

        rows = project_journal._discover_repos(codex_home, 9999)

        rows_by_repo = {pathlib.Path(str(row["repo"])).name: row for row in rows}
        self.assertEqual(set(rows_by_repo), {"first-repo", "second-repo"})
        for row in rows_by_repo.values():
            self.assertEqual(row["rollout_count"], 1)
            self.assertEqual(row["coverage_status"], "complete")

    def test_discover_repos_marks_broken_duplicate_rollout_partial(
        self,
    ) -> None:
        repo = self.init_repo()
        codex_home = self.root / "codex-home"
        active = codex_home / "sessions/2026/05/05"
        archive = codex_home / "archived_sessions"
        active.mkdir(parents=True)
        archive.mkdir(parents=True)
        duplicate_name = "rollout-2026-05-05T10-00-00-duplicate.jsonl"
        (active / duplicate_name).write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        (archive / duplicate_name).write_bytes(b'{"payload":\n')

        rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 1)
        self.assertTrue(os.path.samefile(rows[0]["repo"], repo))
        self.assertEqual(rows[0]["rollout_count"], 1)
        self.assertEqual(rows[0]["coverage_status"], "partial")
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["code"], "discovery_rollout_parse_failed")
        self.assertEqual(coverage["parse_reason"], "invalid_json")
        self.assertEqual(
            pathlib.Path(coverage["source"]).parent.name,
            "archived_sessions",
        )

    def test_discover_repos_marks_oversized_rollout_line_partial(self) -> None:
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-oversized-line.jsonl").write_bytes(b"x" * 65)

        with mock.patch.object(
            project_journal,
            "MAX_DISCOVERY_LINE_BYTES",
            64,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsNone(row["repo"])
        self.assertEqual(row["coverage_status"], "partial")
        self.assertEqual(row["discovery_status"], "inconclusive")
        coverage = row["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["code"], "discovery_limit_exceeded")
        self.assertEqual(coverage["limit_name"], "rollout line bytes")
        self.assertEqual(coverage["limit"], 64)

    def test_discover_repos_marks_deep_json_partial(self) -> None:
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-deep-json.jsonl").write_text(
            json.dumps({"one": {"two": {"cwd": "/tmp/repo"}}}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.object(
            project_journal,
            "MAX_DISCOVERY_JSON_DEPTH",
            2,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(coverage["code"], "discovery_limit_exceeded")
        self.assertEqual(coverage["limit_name"], "JSON nesting depth")
        self.assertEqual(coverage["observed"], 3)

    def test_discover_repos_reports_rollout_parse_failures(self) -> None:
        parse_failures = (
            ("truncated", b'{"payload":{"cwd":"/tmp/repo"}\n', "invalid_json"),
            ("corrupt", b"not-json\n", "invalid_json"),
            (
                "invalid-utf8",
                b'{"payload":{"cwd":"\xff"}}\n',
                "invalid_utf8",
            ),
            (
                "oversized-integer",
                (
                    b'{"payload":{"sequence":'
                    + b"9" * (project_journal.MAX_DISCOVERY_JSON_INTEGER_DIGITS + 1)
                    + b"}}\n"
                ),
                "integer_digit_limit",
            ),
        )
        for name, payload, expected_reason in parse_failures:
            with self.subTest(name=name):
                codex_home = self.root / f"codex-home-{name}"
                rollout_dir = codex_home / "sessions/2026/05/05"
                rollout_dir.mkdir(parents=True)
                rollout = rollout_dir / f"rollout-{name}.jsonl"
                rollout.write_bytes(payload)

                rows = project_journal._discover_repos(codex_home, 9999)

                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertIsNone(row["repo"])
                self.assertEqual(row["coverage_status"], "partial")
                self.assertEqual(row["discovery_status"], "inconclusive")
                coverage = row["discovery_error"]["discovery_coverage"]
                self.assertEqual(
                    coverage["code"],
                    "discovery_rollout_parse_failed",
                )
                self.assertEqual(coverage["parse_reason"], expected_reason)
                self.assertEqual(coverage["record_number"], 1)
                self.assertEqual(coverage["byte_offset"], 0)
                self.assertEqual(coverage["rollout_records_scanned"], 1)
                self.assertEqual(coverage["rollout_bytes_scanned"], len(payload))

    def test_discover_repos_marks_non_regular_rollout_candidates_partial(
        self,
    ) -> None:
        for candidate_kind in ("symlink", "fifo", "directory"):
            with self.subTest(candidate_kind=candidate_kind):
                repo = self.init_repo(f"healthy-repo-{candidate_kind}")
                codex_home = self.root / f"codex-home-stable-{candidate_kind}"
                rollout_dir = codex_home / "sessions/2026/05/05"
                rollout_dir.mkdir(parents=True)
                healthy = rollout_dir / "rollout-healthy.jsonl"
                healthy.write_text(
                    json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
                    encoding="utf-8",
                )
                candidate = rollout_dir / f"rollout-{candidate_kind}.jsonl"
                if candidate_kind == "symlink":
                    target = rollout_dir / "symlink-target"
                    target.write_bytes(b'{"event":"target"}\n')
                    candidate.symlink_to(target)
                elif candidate_kind == "fifo":
                    os.mkfifo(candidate)
                else:
                    candidate.mkdir()

                rows = project_journal._discover_repos(codex_home, 9999)

                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertTrue(os.path.samefile(row["repo"], repo))
                self.assertEqual(row["rollout_count"], 1)
                self.assertEqual(row["coverage_status"], "partial")
                coverage = row["discovery_error"]["discovery_coverage"]
                self.assertEqual(
                    coverage["code"],
                    "discovery_rollout_inspection_failed",
                )
                self.assertEqual(
                    coverage["inspection_reason"],
                    "non_regular_candidate",
                )
                self.assertEqual(pathlib.Path(coverage["source"]), candidate)

    def test_discover_repos_marks_rollout_disappearing_during_enumeration_partial(
        self,
    ) -> None:
        repo = self.init_repo("healthy-repo-enumeration-race")
        codex_home = self.root / "codex-home-enumeration-race"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        healthy = rollout_dir / "rollout-healthy.jsonl"
        healthy.write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        disappearing = rollout_dir / "rollout-disappearing.jsonl"
        disappearing.write_bytes(b'{"event":"disappearing"}\n')
        actual_scandir = project_journal.os.scandir

        with actual_scandir(rollout_dir) as entries:
            healthy_entry = next(
                entry for entry in entries if pathlib.Path(entry.path) == healthy
            )
        vanishing_entry = mock.Mock()
        vanishing_entry.name = disappearing.name
        vanishing_entry.path = str(disappearing)
        vanishing_entry.stat.side_effect = FileNotFoundError(
            errno.ENOENT,
            "injected enumeration race",
            str(disappearing),
        )
        target_scandir = mock.MagicMock()
        target_scandir.__next__.side_effect = [
            healthy_entry,
            vanishing_entry,
            StopIteration,
        ]
        rollout_dir_identity = project_journal._rollout_object_identity(
            rollout_dir.stat()
        )

        def inject_enumeration_race(
            path: int | os.PathLike[str] | str,
        ) -> object:
            if isinstance(path, int):
                identity = project_journal._rollout_object_identity(os.fstat(path))
                if identity == rollout_dir_identity:
                    disappearing.unlink()
                    return target_scandir
            return actual_scandir(path)

        with mock.patch.object(
            project_journal.os,
            "scandir",
            side_effect=inject_enumeration_race,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        self.assertFalse(disappearing.exists())
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsNotNone(row["repo"], row)
        self.assertTrue(os.path.samefile(row["repo"], repo))
        self.assertEqual(row["rollout_count"], 1)
        self.assertEqual(row["coverage_status"], "partial")
        coverage = row["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            coverage["code"],
            "discovery_rollout_inspection_failed",
        )
        self.assertEqual(
            coverage["inspection_reason"],
            "enumeration_revalidation_failed",
        )
        self.assertEqual(coverage["errno"], errno.ENOENT)
        self.assertEqual(pathlib.Path(coverage["source"]), disappearing)
        vanishing_entry.stat.assert_called_once_with(follow_symlinks=False)

    def test_discover_repos_skips_proven_old_non_regular_rollout_candidates(
        self,
    ) -> None:
        repo = self.init_repo("healthy-repo-after-old-candidates")
        codex_home = self.root / "codex-home-old-candidates"
        old_dir = codex_home / "sessions/2000/01/01"
        old_dir.mkdir(parents=True)
        symlink_target = old_dir / "target"
        symlink_target.write_bytes(b'{"event":"target"}\n')
        (old_dir / "rollout-old-symlink.jsonl").symlink_to(symlink_target)
        os.mkfifo(old_dir / "rollout-old-fifo.jsonl")
        (old_dir / "rollout-old-directory.jsonl").mkdir()

        undated_dir = codex_home / "sessions/undated"
        undated_dir.mkdir(parents=True)
        old_mtime_fifo = undated_dir / "rollout-old-mtime.jsonl"
        os.mkfifo(old_mtime_fifo)
        os.utime(old_mtime_fifo, (0, 0))

        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2099-01-01T00-00-00-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )

        rows = project_journal._discover_repos(codex_home, 30)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], repo))
        self.assertEqual(row["rollout_count"], 1)
        self.assertEqual(row["coverage_status"], "complete")

    def test_discover_repos_old_failures_do_not_exhaust_error_budget(
        self,
    ) -> None:
        repo = self.init_repo("healthy-repo-after-old-error-budget")
        codex_home = self.root / "codex-home-old-error-budget"
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2099-01-01T00-00-00-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        original_iterator = project_journal._iter_rollout_paths

        def inject_old_failures(
            root: pathlib.Path,
            state: project_journal._DiscoveryScanState,
            *,
            recurse_directories: bool,
        ) -> object:
            if root == codex_home / "sessions":
                for index in range(project_journal.MAX_DISCOVERY_ERRORS + 1):
                    path = root / "2000/01/01" / f"rollout-missing-{index:02d}.jsonl"
                    yield project_journal._rollout_inspection_failure(
                        path,
                        rollout_root=root,
                        inspection_reason="enumeration_revalidation_failed",
                        detail="injected old missing rollout",
                        error_number=errno.ENOENT,
                    )
            yield from original_iterator(
                root,
                state,
                recurse_directories=recurse_directories,
            )

        with mock.patch.object(
            project_journal,
            "_iter_rollout_paths",
            side_effect=inject_old_failures,
        ):
            rows = project_journal._discover_repos(codex_home, 30)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], repo))
        self.assertEqual(row["rollout_count"], 1)
        self.assertEqual(row["coverage_status"], "complete")

    def test_discover_repos_keeps_window_uncertain_failure_partial(self) -> None:
        repo = self.init_repo("healthy-repo-with-uncertain-failure")
        codex_home = self.root / "codex-home-window-uncertain"
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2099-01-01T00-00-00-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        original_iterator = project_journal._iter_rollout_paths

        def inject_uncertain_failure(
            root: pathlib.Path,
            state: project_journal._DiscoveryScanState,
            *,
            recurse_directories: bool,
        ) -> object:
            if root == codex_home / "sessions":
                yield project_journal._rollout_inspection_failure(
                    root / "unknown/rollout-missing.jsonl",
                    rollout_root=root,
                    inspection_reason="enumeration_revalidation_failed",
                    detail="injected window-uncertain rollout",
                    error_number=errno.ENOENT,
                )
            yield from original_iterator(
                root,
                state,
                recurse_directories=recurse_directories,
            )

        with mock.patch.object(
            project_journal,
            "_iter_rollout_paths",
            side_effect=inject_uncertain_failure,
        ):
            rows = project_journal._discover_repos(codex_home, 30)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], repo))
        self.assertEqual(row["coverage_status"], "partial")
        coverage = row["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            coverage["inspection_reason"],
            "enumeration_revalidation_failed",
        )
        self.assertEqual(
            pathlib.Path(coverage["source"]).name,
            "rollout-missing.jsonl",
        )

    def test_discover_repos_does_not_trust_dated_ancestor_outside_rollout_root(
        self,
    ) -> None:
        repo = self.init_repo("healthy-repo-with-dated-ancestor")
        codex_home = self.root / "sessions/2000/01/01/nested-codex-home"
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2099-01-01T00-00-00-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        original_iterator = project_journal._iter_rollout_paths

        def inject_uncertain_failure(
            root: pathlib.Path,
            state: project_journal._DiscoveryScanState,
            *,
            recurse_directories: bool,
        ) -> object:
            if root == codex_home / "sessions":
                yield project_journal._rollout_inspection_failure(
                    root / "unknown/rollout-missing.jsonl",
                    rollout_root=root,
                    inspection_reason="enumeration_revalidation_failed",
                    detail="injected failure below a dated external ancestor",
                    error_number=errno.ENOENT,
                )
            yield from original_iterator(
                root,
                state,
                recurse_directories=recurse_directories,
            )

        with mock.patch.object(
            project_journal,
            "_iter_rollout_paths",
            side_effect=inject_uncertain_failure,
        ):
            rows = project_journal._discover_repos(codex_home, 30)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], repo))
        self.assertEqual(row["coverage_status"], "partial")
        coverage = row["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            coverage["inspection_reason"],
            "enumeration_revalidation_failed",
        )
        self.assertEqual(
            pathlib.Path(coverage["source"]).name,
            "rollout-missing.jsonl",
        )

    def test_discover_repos_detects_bound_directory_replacement(self) -> None:
        healthy_repo = self.init_repo("healthy-repo-after-directory-replacement")
        replacement_repo = self.init_repo("replacement-tree-repo")
        codex_home = self.root / "codex-home-directory-replacement"
        target = codex_home / "sessions/2099/01/01"
        target.mkdir(parents=True)
        (target / "rollout-original.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(replacement_repo)}}) + "\n",
            encoding="utf-8",
        )
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2099-01-02T00-00-00-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(healthy_repo)}}) + "\n",
            encoding="utf-8",
        )
        actual_open_frame = project_journal._open_rollout_directory_frame
        replaced = False

        def replace_after_binding(
            binding: project_journal._RolloutDirectoryBinding,
            state: project_journal._DiscoveryScanState,
        ) -> object:
            nonlocal replaced
            if binding.path == target and not replaced:
                replaced = True
                detached = codex_home / "detached-day"
                target.rename(detached)
                target.mkdir()
                (target / "rollout-substituted.jsonl").write_text(
                    json.dumps({"payload": {"cwd": str(replacement_repo)}}) + "\n",
                    encoding="utf-8",
                )
            return actual_open_frame(binding, state)

        with mock.patch.object(
            project_journal,
            "_open_rollout_directory_frame",
            side_effect=replace_after_binding,
        ):
            rows = project_journal._discover_repos(codex_home, 30)

        self.assertTrue(replaced)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], healthy_repo))
        self.assertEqual(row["coverage_status"], "partial")
        coverage = row["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["inspection_reason"], "directory_replaced")
        self.assertEqual(pathlib.Path(coverage["source"]), target)

    def test_discover_repos_prefers_ancestor_replacement_to_child_open_error(
        self,
    ) -> None:
        healthy_repo = self.init_repo("healthy-repo-after-child-open-race")
        untrusted_repo = self.init_repo("untrusted-repo-after-child-open-race")
        codex_home = self.root / "codex-home-child-open-race"
        year = codex_home / "sessions/2099"
        month = year / "01"
        target = month / "01"
        target.mkdir(parents=True)
        (target / "rollout-untrusted.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(untrusted_repo)}}) + "\n",
            encoding="utf-8",
        )
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2099-01-02T00-00-00-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(healthy_repo)}}) + "\n",
            encoding="utf-8",
        )
        month_identity = project_journal._rollout_object_identity(month.stat())
        actual_open = project_journal.os.open
        mutated = False

        def replace_ancestor_and_deny_child_open(
            path: os.PathLike[str] | str,
            flags: int,
            mode: int = 0o777,
            *,
            dir_fd: int | None = None,
        ) -> int:
            nonlocal mutated
            if (
                not mutated
                and dir_fd is not None
                and os.fspath(path) == target.name
                and project_journal._rollout_object_identity(os.fstat(dir_fd))
                == month_identity
            ):
                mutated = True
                year.rename(codex_home / "detached-year-after-open-race")
                year.mkdir()
                raise PermissionError(
                    errno.EACCES,
                    "injected child directory open denial",
                    os.fspath(path),
                )
            return actual_open(
                path,
                flags,
                mode,
                dir_fd=dir_fd,
            )

        with mock.patch.object(
            project_journal.os,
            "open",
            side_effect=replace_ancestor_and_deny_child_open,
        ):
            with mock.patch.object(project_journal, "MAX_DISCOVERY_ERRORS", 1):
                rows = project_journal._discover_repos(codex_home, 30)

        self.assertTrue(mutated)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], healthy_repo))
        self.assertEqual(row["coverage_status"], "partial")
        coverage = row["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["inspection_reason"], "directory_replaced")
        self.assertEqual(pathlib.Path(coverage["source"]), year)
        self.assertNotIn("errno", coverage)

    def test_discover_repos_revalidates_ancestors_before_scan_error_handoff(
        self,
    ) -> None:
        healthy_repo = self.init_repo("healthy-repo-after-ancestor-race")
        untrusted_repo = self.init_repo("untrusted-repo-after-ancestor-race")
        codex_home = self.root / "codex-home-ancestor-scan-race"
        year = codex_home / "sessions/2099"
        target = year / "01/01"
        target.mkdir(parents=True)
        (target / "rollout-untrusted.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(untrusted_repo)}}) + "\n",
            encoding="utf-8",
        )
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2099-01-02T00-00-00-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(healthy_repo)}}) + "\n",
            encoding="utf-8",
        )
        target_identity = project_journal._rollout_object_identity(target.stat())
        actual_scandir = project_journal.os.scandir
        actual_close = project_journal.os.close
        target_scandir = mock.MagicMock()
        mutated = False
        close_failed = False

        def replace_ancestor_and_fail() -> object:
            nonlocal mutated
            mutated = True
            detached = codex_home / "detached-year"
            year.rename(detached)
            year.mkdir()
            raise OSError(errno.EIO, "injected directory scan failure")

        target_scandir.__next__.side_effect = replace_ancestor_and_fail

        def inject_scan_failure(
            path: int | os.PathLike[str] | str,
        ) -> object:
            if isinstance(path, int):
                identity = project_journal._rollout_object_identity(os.fstat(path))
                if identity == target_identity:
                    return target_scandir
            return actual_scandir(path)

        def close_target_then_fail(fd: int) -> None:
            nonlocal close_failed
            identity = project_journal._rollout_object_identity(os.fstat(fd))
            if identity == target_identity and not close_failed:
                actual_close(fd)
                close_failed = True
                raise OSError(
                    errno.EIO,
                    "injected directory descriptor close failure",
                )
            actual_close(fd)

        with mock.patch.object(
            project_journal.os,
            "scandir",
            side_effect=inject_scan_failure,
        ):
            with mock.patch.object(
                project_journal.os,
                "close",
                side_effect=close_target_then_fail,
            ):
                rows = project_journal._discover_repos(codex_home, 30)

        self.assertTrue(mutated)
        self.assertTrue(close_failed)
        target_scandir.close.assert_called_once_with()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], healthy_repo))
        self.assertEqual(row["coverage_status"], "partial")
        coverage = row["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["inspection_reason"], "directory_replaced")
        self.assertEqual(pathlib.Path(coverage["source"]), year)
        self.assertEqual(len(coverage["cleanup_errors"]), 1)
        cleanup = coverage["cleanup_errors"][0]
        self.assertEqual(cleanup["errno"], errno.EIO)
        self.assertIn("descriptor close failure", cleanup["message"])
        self.assertIn(str(target), cleanup["context"])

    def test_discover_repos_prefers_ancestor_policy_change_to_entry_stat_error(
        self,
    ) -> None:
        healthy_repo = self.init_repo("healthy-repo-after-entry-stat-race")
        codex_home = self.root / "codex-home-entry-stat-race"
        year = codex_home / "sessions/2099"
        target = year / "01/01"
        target.mkdir(parents=True)
        year.chmod(0o755)
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2099-01-02T00-00-00-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(healthy_repo)}}) + "\n",
            encoding="utf-8",
        )
        target_identity = project_journal._rollout_object_identity(target.stat())
        actual_scandir = project_journal.os.scandir
        target_scandir = mock.MagicMock()
        failing_entry = mock.Mock()
        failing_entry.name = "rollout-unreadable.jsonl"
        policy_changed = False

        def change_ancestor_policy_and_fail_stat(
            *,
            follow_symlinks: bool,
        ) -> os.stat_result:
            nonlocal policy_changed
            self.assertFalse(follow_symlinks)
            policy_changed = True
            year.chmod(0o700)
            raise OSError(errno.EIO, "injected rollout entry stat failure")

        failing_entry.stat.side_effect = change_ancestor_policy_and_fail_stat
        target_scandir.__next__.side_effect = [
            failing_entry,
            StopIteration,
        ]

        def inject_entry_stat_race(
            path: int | os.PathLike[str] | str,
        ) -> object:
            if isinstance(path, int):
                identity = project_journal._rollout_object_identity(os.fstat(path))
                if identity == target_identity:
                    return target_scandir
            return actual_scandir(path)

        with mock.patch.object(
            project_journal.os,
            "scandir",
            side_effect=inject_entry_stat_race,
        ):
            with mock.patch.object(project_journal, "MAX_DISCOVERY_ERRORS", 1):
                rows = project_journal._discover_repos(codex_home, 30)

        self.assertTrue(policy_changed)
        target_scandir.close.assert_called_once_with()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], healthy_repo))
        self.assertEqual(row["coverage_status"], "partial")
        coverage = row["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            coverage["inspection_reason"],
            "directory_access_policy_changed",
        )
        self.assertEqual(pathlib.Path(coverage["source"]), year)
        self.assertNotIn("errno", coverage)

    def test_discover_repos_detects_bound_directory_access_policy_change(
        self,
    ) -> None:
        healthy_repo = self.init_repo("healthy-repo-after-directory-chmod")
        codex_home = self.root / "codex-home-directory-chmod"
        target = codex_home / "sessions/2099/01/01"
        target.mkdir(parents=True)
        (target / "rollout-untrusted.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(healthy_repo)}}) + "\n",
            encoding="utf-8",
        )
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        (archive / "rollout-2099-01-02T00-00-00-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(healthy_repo)}}) + "\n",
            encoding="utf-8",
        )
        actual_open_frame = project_journal._open_rollout_directory_frame
        changed = False

        def chmod_after_binding(
            binding: project_journal._RolloutDirectoryBinding,
            state: project_journal._DiscoveryScanState,
        ) -> object:
            nonlocal changed
            if binding.path == target and not changed:
                changed = True
                target.chmod(0o700)
            return actual_open_frame(binding, state)

        with mock.patch.object(
            project_journal,
            "_open_rollout_directory_frame",
            side_effect=chmod_after_binding,
        ):
            rows = project_journal._discover_repos(codex_home, 30)

        self.assertTrue(changed)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], healthy_repo))
        self.assertEqual(row["coverage_status"], "partial")
        coverage = row["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            coverage["inspection_reason"],
            "directory_access_policy_changed",
        )
        self.assertEqual(pathlib.Path(coverage["source"]), target)

    def test_discover_repos_distinguishes_missing_and_symlink_directory_races(
        self,
    ) -> None:
        actual_open = project_journal.os.open
        for mutation, expected_reason in (
            ("missing", "directory_missing"),
            ("symlink", "directory_replaced"),
        ):
            with self.subTest(mutation=mutation):
                healthy_repo = self.init_repo(f"healthy-repo-{mutation}")
                replacement_repo = self.init_repo(f"replacement-repo-{mutation}")
                codex_home = self.root / f"codex-home-directory-{mutation}"
                sessions = codex_home / "sessions"
                year = sessions / "2099"
                rollout_dir = year / "01/01"
                rollout_dir.mkdir(parents=True)
                (rollout_dir / "rollout-untrusted.jsonl").write_text(
                    json.dumps({"payload": {"cwd": str(replacement_repo)}}) + "\n",
                    encoding="utf-8",
                )
                archive = codex_home / "archived_sessions"
                archive.mkdir(parents=True)
                (archive / "rollout-2099-01-02T00-00-00-healthy.jsonl").write_text(
                    json.dumps({"payload": {"cwd": str(healthy_repo)}}) + "\n",
                    encoding="utf-8",
                )
                sessions_identity = project_journal._rollout_object_identity(
                    sessions.stat()
                )
                mutated = False

                def replace_before_directory_open(
                    path: os.PathLike[str] | str,
                    flags: int,
                    mode: int = 0o777,
                    *,
                    dir_fd: int | None = None,
                ) -> int:
                    nonlocal mutated
                    if (
                        not mutated
                        and dir_fd is not None
                        and os.fspath(path) == year.name
                        and project_journal._rollout_object_identity(os.fstat(dir_fd))
                        == sessions_identity
                    ):
                        mutated = True
                        detached = codex_home / f"detached-year-{mutation}"
                        year.rename(detached)
                        if mutation == "symlink":
                            year.symlink_to(detached, target_is_directory=True)
                    return actual_open(
                        path,
                        flags,
                        mode,
                        dir_fd=dir_fd,
                    )

                with mock.patch.object(
                    project_journal.os,
                    "open",
                    side_effect=replace_before_directory_open,
                ):
                    rows = project_journal._discover_repos(codex_home, 30)

                self.assertTrue(mutated)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertTrue(os.path.samefile(row["repo"], healthy_repo))
                self.assertEqual(row["coverage_status"], "partial")
                coverage = row["discovery_error"]["discovery_coverage"]
                self.assertEqual(
                    coverage["inspection_reason"],
                    expected_reason,
                )
                self.assertEqual(pathlib.Path(coverage["source"]), year)

    def test_discover_repos_allows_directory_timestamp_and_child_churn(
        self,
    ) -> None:
        repo = self.init_repo("healthy-repo-directory-churn")
        codex_home = self.root / "codex-home-directory-churn"
        target = codex_home / "sessions/2099/01/01"
        target.mkdir(parents=True)
        (target / "rollout-healthy.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        actual_revalidate = project_journal._revalidate_rollout_directory
        churned = False

        def add_benign_churn(
            binding: project_journal._RolloutDirectoryBinding,
            state: project_journal._DiscoveryScanState,
        ) -> object:
            nonlocal churned
            if binding.path == target and not churned:
                churned = True
                os.utime(target, None)
                transient = target / "transient-entry"
                transient.write_bytes(b"transient")
                transient.unlink()
            return actual_revalidate(binding, state)

        with mock.patch.object(
            project_journal,
            "_revalidate_rollout_directory",
            side_effect=add_benign_churn,
        ):
            rows = project_journal._discover_repos(codex_home, 30)

        self.assertTrue(churned)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(os.path.samefile(row["repo"], repo))
        self.assertEqual(row["coverage_status"], "complete")

    def test_discover_repos_rejects_fifo_and_symlink_rollout_replacement(
        self,
    ) -> None:
        original_iterator = project_journal._iter_rollout_paths
        for replacement_kind, expected_reason in (
            ("fifo", "non_regular_replacement"),
            ("symlink", "symlink_replacement"),
        ):
            with self.subTest(replacement_kind=replacement_kind):
                codex_home = self.root / f"codex-home-{replacement_kind}"
                rollout_dir = codex_home / "sessions/2026/05/05"
                rollout_dir.mkdir(parents=True)
                rollout = rollout_dir / "rollout-replaced.jsonl"
                payload = b'{"event":"stable"}\n'
                rollout.write_bytes(payload)
                symlink_target = rollout_dir / "target.jsonl"
                symlink_target.write_bytes(payload)

                def replace_after_enumeration(
                    root: pathlib.Path,
                    state: project_journal._DiscoveryScanState,
                    *,
                    recurse_directories: bool,
                ) -> object:
                    for candidate in original_iterator(
                        root,
                        state,
                        recurse_directories=recurse_directories,
                    ):
                        if candidate.path == rollout:
                            rollout.unlink()
                            if replacement_kind == "fifo":
                                os.mkfifo(rollout)
                            else:
                                rollout.symlink_to(symlink_target)
                        yield candidate

                started = time.monotonic()
                with mock.patch.object(
                    project_journal,
                    "_iter_rollout_paths",
                    side_effect=replace_after_enumeration,
                ):
                    rows = project_journal._discover_repos(codex_home, 9999)
                elapsed = time.monotonic() - started

                self.assertLess(elapsed, 1.0)
                self.assertEqual(len(rows), 1)
                row = rows[0]
                self.assertIsNone(row["repo"])
                self.assertEqual(row["coverage_status"], "partial")
                coverage = row["discovery_error"]["discovery_coverage"]
                self.assertEqual(
                    coverage["code"],
                    "discovery_rollout_inspection_failed",
                )
                self.assertEqual(
                    coverage["inspection_reason"],
                    expected_reason,
                )
                self.assertEqual(pathlib.Path(coverage["source"]), rollout)

    def test_discover_repos_classifies_same_object_drift_after_enumeration(
        self,
    ) -> None:
        original_iterator = project_journal._iter_rollout_paths
        for mutation, expected_reason in (
            ("append", "content_changed"),
            ("truncate", "content_changed"),
            ("chmod", "access_policy_changed"),
        ):
            with self.subTest(mutation=mutation):
                codex_home = self.root / f"codex-home-enumerated-{mutation}"
                rollout_dir = codex_home / "sessions/2026/05/05"
                rollout_dir.mkdir(parents=True)
                rollout = rollout_dir / f"rollout-{mutation}.jsonl"
                payload = b'{"event":"stable"}\n'
                rollout.write_bytes(payload)

                def mutate_after_enumeration(
                    root: pathlib.Path,
                    state: project_journal._DiscoveryScanState,
                    *,
                    recurse_directories: bool,
                ) -> object:
                    for candidate in original_iterator(
                        root,
                        state,
                        recurse_directories=recurse_directories,
                    ):
                        if candidate.path == rollout:
                            if mutation == "append":
                                with rollout.open("ab") as handle:
                                    handle.write(b'{"event":"appended"}\n')
                            elif mutation == "truncate":
                                os.truncate(rollout, len(payload) - 1)
                            else:
                                current_mode = stat.S_IMODE(rollout.stat().st_mode)
                                os.chmod(rollout, current_mode ^ stat.S_IXUSR)
                        yield candidate

                with mock.patch.object(
                    project_journal,
                    "_iter_rollout_paths",
                    side_effect=mutate_after_enumeration,
                ):
                    rows = project_journal._discover_repos(codex_home, 9999)

                self.assertEqual(rows[0]["coverage_status"], "partial")
                self.assertEqual(rows[0]["discovery_status"], "inconclusive")
                coverage = rows[0]["discovery_error"]["discovery_coverage"]
                self.assertEqual(
                    coverage["code"],
                    "discovery_rollout_inspection_failed",
                )
                self.assertEqual(
                    coverage["inspection_reason"],
                    expected_reason,
                )

    def test_discover_repos_reports_unreadable_rollout_distinctly(self) -> None:
        codex_home = self.root / "codex-home-unreadable-rollout"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        rollout = rollout_dir / "rollout-unreadable.jsonl"
        rollout.write_bytes(b'{"event":"stable"}\n')
        actual_open = project_journal.os.open

        def deny_rollout_open(
            path: os.PathLike[str] | str,
            flags: int,
            *args: object,
            **kwargs: object,
        ) -> int:
            if pathlib.Path(path) == rollout:
                raise PermissionError(
                    errno.EACCES,
                    "injected unreadable rollout",
                    str(path),
                )
            return actual_open(path, flags, *args, **kwargs)

        with mock.patch.object(
            project_journal.os,
            "open",
            side_effect=deny_rollout_open,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            coverage["code"],
            "discovery_rollout_inspection_failed",
        )
        self.assertEqual(coverage["inspection_reason"], "unreadable")
        self.assertEqual(coverage["errno"], errno.EACCES)
        self.assertEqual(coverage["error_name"], "EACCES")

    def test_discover_repos_detects_concurrent_rollout_content_change(
        self,
    ) -> None:
        codex_home = self.root / "codex-home-content-change"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        rollout = rollout_dir / "rollout-content-change.jsonl"
        first_cwd = str(self.root / "candidate-a")
        second_cwd = str(self.root / "candidate-b")
        first_payload = (
            json.dumps(
                {"payload": {"cwd": first_cwd}},
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        second_payload = (
            json.dumps(
                {"payload": {"cwd": second_cwd}},
                separators=(",", ":"),
            )
            + "\n"
        ).encode()
        self.assertEqual(len(first_payload), len(second_payload))
        rollout.write_bytes(first_payload)
        original_verifier = project_journal._read_rollout_verification_digest
        changed = False

        def mutate_before_verification(
            fd: int,
            candidate: project_journal._RolloutCandidate,
            expected_bytes: int,
            state: project_journal._DiscoveryScanState,
        ) -> tuple[bytes, int]:
            nonlocal changed
            if not changed:
                changed = True
                candidate.path.write_bytes(second_payload)
            return original_verifier(
                fd,
                candidate,
                expected_bytes,
                state,
            )

        with mock.patch.object(
            project_journal,
            "_read_rollout_verification_digest",
            side_effect=mutate_before_verification,
        ):
            with mock.patch.object(
                project_journal,
                "_repo_root_for_path",
            ) as resolver:
                with mock.patch.object(
                    project_journal,
                    "_enrich_discovered_repo",
                ) as enrich:
                    rows = project_journal._discover_repos(codex_home, 9999)

        self.assertTrue(changed)
        resolver.assert_not_called()
        enrich.assert_not_called()
        self.assertIsNone(rows[0]["repo"])
        self.assertIsNone(rows[0]["candidate_cwd"])
        self.assertEqual(rows[0]["rollout_count"], 0)
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(rows[0]["discovery_status"], "inconclusive")
        self.assertEqual(rows[0]["adoption_status"], "inconclusive")
        self.assertIsNone(rows[0]["tracked_journal_adopted"])
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            coverage["code"],
            "discovery_rollout_inspection_failed",
        )
        self.assertEqual(coverage["inspection_reason"], "content_changed")
        self.assertEqual(coverage["rollout_bytes_scanned"], rollout.stat().st_size)
        self.assertEqual(coverage["distinct_cwds_scanned"], 1)
        self.assertEqual(coverage["rollout_associations_counted"], 0)

    def test_discover_repos_classifies_append_and_truncation_as_content_change(
        self,
    ) -> None:
        original_verifier = project_journal._read_rollout_verification_digest
        for mutation in ("append", "truncate"):
            with self.subTest(mutation=mutation):
                codex_home = self.root / f"codex-home-{mutation}"
                rollout_dir = codex_home / "sessions/2026/05/05"
                rollout_dir.mkdir(parents=True)
                rollout = rollout_dir / f"rollout-{mutation}.jsonl"
                payload = b'{"event":"stable"}\n'
                rollout.write_bytes(payload)
                changed = False

                def mutate_before_verification(
                    fd: int,
                    candidate: project_journal._RolloutCandidate,
                    expected_bytes: int,
                    state: project_journal._DiscoveryScanState,
                ) -> tuple[bytes, int]:
                    nonlocal changed
                    if not changed:
                        changed = True
                        if mutation == "append":
                            with candidate.path.open("ab") as handle:
                                handle.write(b'{"event":"appended"}\n')
                        else:
                            os.truncate(candidate.path, len(payload) - 1)
                    return original_verifier(
                        fd,
                        candidate,
                        expected_bytes,
                        state,
                    )

                with mock.patch.object(
                    project_journal,
                    "_read_rollout_verification_digest",
                    side_effect=mutate_before_verification,
                ):
                    rows = project_journal._discover_repos(codex_home, 9999)

                self.assertTrue(changed)
                self.assertEqual(rows[0]["coverage_status"], "partial")
                self.assertEqual(rows[0]["discovery_status"], "inconclusive")
                coverage = rows[0]["discovery_error"]["discovery_coverage"]
                self.assertEqual(
                    coverage["code"],
                    "discovery_rollout_inspection_failed",
                )
                self.assertEqual(
                    coverage["inspection_reason"],
                    "content_changed",
                )

    def test_discover_repos_classifies_chmod_as_access_policy_change(
        self,
    ) -> None:
        codex_home = self.root / "codex-home-chmod"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        rollout = rollout_dir / "rollout-chmod.jsonl"
        rollout.write_bytes(b'{"event":"stable"}\n')
        original_verifier = project_journal._read_rollout_verification_digest
        changed = False

        def chmod_before_verification(
            fd: int,
            candidate: project_journal._RolloutCandidate,
            expected_bytes: int,
            state: project_journal._DiscoveryScanState,
        ) -> tuple[bytes, int]:
            nonlocal changed
            if not changed:
                changed = True
                current_mode = stat.S_IMODE(candidate.path.stat().st_mode)
                os.chmod(candidate.path, current_mode ^ stat.S_IXUSR)
            return original_verifier(
                fd,
                candidate,
                expected_bytes,
                state,
            )

        with mock.patch.object(
            project_journal,
            "_read_rollout_verification_digest",
            side_effect=chmod_before_verification,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        self.assertTrue(changed)
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(rows[0]["discovery_status"], "inconclusive")
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            coverage["code"],
            "discovery_rollout_inspection_failed",
        )
        self.assertEqual(
            coverage["inspection_reason"],
            "access_policy_changed",
        )

    def test_discover_repos_classifies_owner_and_group_as_access_policy_change(
        self,
    ) -> None:
        original_verifier = project_journal._read_rollout_verification_digest
        original_fstat = project_journal.os.fstat
        for policy_field in ("owner", "group"):
            with self.subTest(policy_field=policy_field):
                codex_home = self.root / f"codex-home-{policy_field}"
                rollout_dir = codex_home / "sessions/2026/05/05"
                rollout_dir.mkdir(parents=True)
                rollout = rollout_dir / f"rollout-{policy_field}.jsonl"
                rollout.write_bytes(b'{"event":"stable"}\n')
                rollout_object = project_journal._rollout_object_identity(
                    rollout.stat()
                )
                changed = False

                def mutate_before_verification(
                    fd: int,
                    candidate: project_journal._RolloutCandidate,
                    expected_bytes: int,
                    state: project_journal._DiscoveryScanState,
                ) -> tuple[bytes, int]:
                    nonlocal changed
                    changed = True
                    return original_verifier(
                        fd,
                        candidate,
                        expected_bytes,
                        state,
                    )

                def changed_fstat(fd: int) -> os.stat_result:
                    value = original_fstat(fd)
                    if (
                        changed
                        and project_journal._rollout_object_identity(value)
                        == rollout_object
                    ):
                        if policy_field == "owner":
                            return stat_with_uid(value, value.st_uid + 1)
                        return stat_with_gid(value, value.st_gid + 1)
                    return value

                with mock.patch.object(
                    project_journal,
                    "_read_rollout_verification_digest",
                    side_effect=mutate_before_verification,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "fstat",
                        side_effect=changed_fstat,
                    ):
                        rows = project_journal._discover_repos(codex_home, 9999)

                self.assertTrue(changed)
                self.assertEqual(rows[0]["coverage_status"], "partial")
                self.assertEqual(rows[0]["discovery_status"], "inconclusive")
                coverage = rows[0]["discovery_error"]["discovery_coverage"]
                self.assertEqual(
                    coverage["code"],
                    "discovery_rollout_inspection_failed",
                )
                self.assertEqual(
                    coverage["inspection_reason"],
                    "access_policy_changed",
                )

    def test_discover_repos_detects_concurrent_rollout_object_replacement(
        self,
    ) -> None:
        codex_home = self.root / "codex-home-object-change"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        rollout = rollout_dir / "rollout-object-change.jsonl"
        payload = b'{"event":"stable"}\n'
        rollout.write_bytes(payload)
        replacement = rollout_dir / "replacement.jsonl"
        replacement.write_bytes(payload)
        original_verifier = project_journal._read_rollout_verification_digest
        replaced = False

        def replace_before_verification(
            fd: int,
            candidate: project_journal._RolloutCandidate,
            expected_bytes: int,
            state: project_journal._DiscoveryScanState,
        ) -> tuple[bytes, int]:
            nonlocal replaced
            if not replaced:
                replaced = True
                os.replace(replacement, candidate.path)
            return original_verifier(
                fd,
                candidate,
                expected_bytes,
                state,
            )

        with mock.patch.object(
            project_journal,
            "_read_rollout_verification_digest",
            side_effect=replace_before_verification,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        self.assertTrue(replaced)
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(rows[0]["discovery_status"], "inconclusive")
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(
            coverage["code"],
            "discovery_rollout_inspection_failed",
        )
        self.assertEqual(coverage["inspection_reason"], "path_replaced")
        self.assertEqual(pathlib.Path(coverage["source"]), rollout)

    def test_discover_repos_discards_pending_cwds_when_later_record_is_invalid(
        self,
    ) -> None:
        repo = self.init_repo()
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        first_record = (json.dumps({"payload": {"cwd": str(repo)}}) + "\n").encode()
        (rollout_dir / "rollout-late-invalid.jsonl").write_bytes(
            first_record + b'{"payload":\n'
        )

        with mock.patch.object(
            project_journal,
            "_repo_root_for_path",
        ) as resolver:
            rows = project_journal._discover_repos(codex_home, 9999)

        resolver.assert_not_called()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["repo"])
        self.assertIsNone(rows[0]["candidate_cwd"])
        self.assertEqual(rows[0]["rollout_count"], 0)
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(rows[0]["adoption_status"], "inconclusive")
        self.assertIsNone(rows[0]["tracked_journal_adopted"])
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["parse_reason"], "invalid_json")
        self.assertEqual(coverage["record_number"], 2)
        self.assertEqual(coverage["byte_offset"], len(first_record))
        self.assertEqual(coverage["rollout_associations_counted"], 0)

    def test_normalize_discovery_cwd_applies_caps_before_path_construction(
        self,
    ) -> None:
        cases = (
            (
                "utf8-bytes",
                "/éé",
                "MAX_DISCOVERY_CWD_UTF8_BYTES",
                4,
                project_journal.DiscoveryLimitExceeded,
            ),
            (
                "components",
                "/one/two/three",
                "MAX_DISCOVERY_CWD_COMPONENTS",
                2,
                project_journal.DiscoveryLimitExceeded,
            ),
            (
                "invalid-utf8",
                "\ud800",
                "MAX_DISCOVERY_CWD_UTF8_BYTES",
                project_journal.MAX_DISCOVERY_CWD_UTF8_BYTES,
                project_journal.DiscoveryCwdValidationError,
            ),
            (
                "nul-byte",
                "/repo/\x00",
                "MAX_DISCOVERY_CWD_UTF8_BYTES",
                project_journal.MAX_DISCOVERY_CWD_UTF8_BYTES,
                project_journal.DiscoveryCwdValidationError,
            ),
        )
        for name, cwd, constant, limit, expected_error in cases:
            with self.subTest(name=name):
                with mock.patch.object(project_journal, constant, limit):
                    with mock.patch.object(
                        project_journal.pathlib,
                        "Path",
                    ) as path_constructor:
                        with self.assertRaises(expected_error):
                            project_journal._normalize_discovery_cwd(cwd)
                        path_constructor.assert_not_called()

    def test_discover_repos_marks_oversized_cwd_bytes_partial(self) -> None:
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-oversized-cwd.jsonl").write_text(
            json.dumps({"payload": {"cwd": "/éé"}}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.object(
            project_journal,
            "MAX_DISCOVERY_CWD_UTF8_BYTES",
            4,
        ):
            with mock.patch.object(
                project_journal,
                "_repo_root_for_path",
            ) as resolver:
                rows = project_journal._discover_repos(codex_home, 9999)

        resolver.assert_not_called()
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(coverage["code"], "discovery_limit_exceeded")
        self.assertEqual(coverage["limit_name"], "CWD UTF-8 bytes")
        self.assertEqual(coverage["observed"], 5)

    def test_discover_repos_marks_excessive_cwd_components_partial(
        self,
    ) -> None:
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-deep-cwd.jsonl").write_text(
            json.dumps({"payload": {"cwd": "/one/two/three"}}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.object(
            project_journal,
            "MAX_DISCOVERY_CWD_COMPONENTS",
            2,
        ):
            with mock.patch.object(
                project_journal,
                "_repo_root_for_path",
            ) as resolver:
                rows = project_journal._discover_repos(codex_home, 9999)

        resolver.assert_not_called()
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(coverage["code"], "discovery_limit_exceeded")
        self.assertEqual(coverage["limit_name"], "CWD component count")
        self.assertEqual(coverage["observed"], 3)

    def test_discover_repos_marks_invalid_utf8_cwd_partial(self) -> None:
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-invalid-cwd.jsonl").write_text(
            json.dumps({"payload": {"cwd": "\ud800"}}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.object(
            project_journal,
            "_repo_root_for_path",
        ) as resolver:
            rows = project_journal._discover_repos(codex_home, 9999)

        resolver.assert_not_called()
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(coverage["code"], "discovery_cwd_invalid")
        self.assertEqual(coverage["validation_reason"], "invalid_utf8")

    def test_discover_repos_rejects_nul_cwd_before_repo_resolution(self) -> None:
        repo = self.init_repo().resolve()
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-nul-cwd.jsonl").write_text(
            json.dumps({"payload": {"cwd": f"{repo}/\x00"}}) + "\n",
            encoding="utf-8",
        )

        with mock.patch.object(
            project_journal,
            "_repo_root_for_path",
        ) as resolver:
            rows = project_journal._discover_repos(codex_home, 9999)

        resolver.assert_not_called()
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0]["repo"])
        self.assertIsNone(rows[0]["candidate_cwd"])
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(rows[0]["discovery_status"], "inconclusive")
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["code"], "discovery_cwd_invalid")
        self.assertEqual(coverage["validation_reason"], "nul_byte")
        self.assertEqual(coverage["distinct_cwds_scanned"], 0)

    def test_discover_repos_normalizes_cwd_aliases_before_resolution(self) -> None:
        repo = self.init_repo().resolve()
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        aliases = [
            str(repo),
            f"{repo}/.",
            f"{repo}//",
            str(repo),
        ]
        (rollout_dir / "rollout-aliases.jsonl").write_text(
            "".join(
                json.dumps({"payload": {"cwd": alias}}) + "\n" for alias in aliases
            ),
            encoding="utf-8",
        )
        resolution_calls: list[str] = []

        def resolve_candidate(
            path_text: str,
            *,
            codex_home: pathlib.Path | None = None,
            deadline: float | None = None,
        ) -> pathlib.Path:
            del codex_home, deadline
            resolution_calls.append(path_text)
            return repo

        def enrich_candidate(
            root: pathlib.Path,
            row: dict[str, object],
            script: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> None:
            del root, script, deadline
            row.update(
                {
                    "adoption_status": "unadopted",
                    "adoption_error": None,
                    "discovery_status": "complete",
                    "discovery_error": None,
                }
            )

        with mock.patch.object(
            project_journal,
            "_repo_root_for_path",
            side_effect=resolve_candidate,
        ):
            with mock.patch.object(
                project_journal,
                "_enrich_discovered_repo",
                side_effect=enrich_candidate,
            ):
                rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(resolution_calls, [str(repo)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rollout_count"], 1)

    def test_discover_repos_commits_distinct_cwds_after_rollout_revalidation(
        self,
    ) -> None:
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        first = str(self.root / "candidate-b")
        second = str(self.root / "candidate-a")
        (rollout_dir / "rollout-buffered-cwds.jsonl").write_text(
            "".join(
                json.dumps({"payload": {"cwd": cwd}}) + "\n"
                for cwd in (first, second, first)
            ),
            encoding="utf-8",
        )
        actual_open_candidate = project_journal._open_rollout_candidate
        actual_revalidate = project_journal._revalidate_rollout_candidate
        actual_close = project_journal.os.close
        candidate_fd: int | None = None
        events: list[str] = []

        def capture_candidate_fd(
            candidate: project_journal._RolloutCandidate,
            state: project_journal._DiscoveryScanState,
        ) -> int:
            nonlocal candidate_fd
            candidate_fd = actual_open_candidate(candidate, state)
            return candidate_fd

        def record_revalidation(*args: object, **kwargs: object) -> None:
            events.append("revalidate")
            actual_revalidate(*args, **kwargs)

        def record_close(fd: int) -> None:
            if candidate_fd is not None and fd == candidate_fd:
                events.append("close")
            actual_close(fd)

        def resolve_candidate(
            path_text: str,
            *,
            codex_home: pathlib.Path | None = None,
            deadline: float | None = None,
        ) -> None:
            del codex_home, deadline
            events.append(f"resolve:{path_text}")
            return None

        with mock.patch.object(
            project_journal,
            "_open_rollout_candidate",
            side_effect=capture_candidate_fd,
        ):
            with mock.patch.object(
                project_journal,
                "_revalidate_rollout_candidate",
                side_effect=record_revalidation,
            ):
                with mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=record_close,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_repo_root_for_path",
                        side_effect=resolve_candidate,
                    ):
                        rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(rows, [])
        self.assertEqual(
            events,
            [
                "revalidate",
                "close",
                f"resolve:{first}",
                f"resolve:{second}",
            ],
        )

    def test_discover_repos_bounds_distinct_cwds(self) -> None:
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-many-cwds.jsonl").write_text(
            "".join(
                json.dumps({"payload": {"cwd": f"/tmp/candidate-{index}"}}) + "\n"
                for index in range(3)
            ),
            encoding="utf-8",
        )

        with mock.patch.object(
            project_journal,
            "MAX_DISCOVERY_DISTINCT_CWDS",
            2,
        ):
            with mock.patch.object(
                project_journal,
                "_repo_root_for_path",
                return_value=None,
            ) as resolver:
                rows = project_journal._discover_repos(codex_home, 9999)

        resolver.assert_not_called()
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertIsNone(rows[0]["repo"])
        self.assertIsNone(rows[0]["candidate_cwd"])
        self.assertEqual(rows[0]["rollout_count"], 0)
        self.assertEqual(rows[0]["coverage_status"], "partial")
        self.assertEqual(coverage["limit_name"], "distinct CWD count")
        self.assertEqual(coverage["observed"], 3)
        self.assertEqual(coverage["distinct_cwds_scanned"], 2)
        self.assertEqual(coverage["rollout_associations_counted"], 0)

    def test_discover_repos_bounds_aggregate_rollout_associations(self) -> None:
        repo = self.init_repo("association-repo").resolve()
        resolved_cwd = str(self.root / "resolved-candidate")
        unresolved_cwd = str(self.root / "unresolved-candidate")
        codex_home = self.root / "codex-home-association-cap"
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        payload = "".join(
            (
                json.dumps({"payload": {"cwd": resolved_cwd}}) + "\n",
                json.dumps({"payload": {"cwd": unresolved_cwd}}) + "\n",
            )
        )
        for index in range(2):
            (
                archive / f"rollout-2026-05-0{index + 6}T12-34-56-association.jsonl"
            ).write_text(payload, encoding="utf-8")

        def resolve_candidate(
            path_text: str,
            *,
            codex_home: pathlib.Path | None = None,
            deadline: float | None = None,
        ) -> pathlib.Path:
            del codex_home, deadline
            if path_text == resolved_cwd:
                return repo
            raise project_journal.UserError("injected repository resolution failure")

        def enrich_candidate(
            root: pathlib.Path,
            row: dict[str, object],
            script: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> None:
            del root, script, deadline
            row.update(
                {
                    "adoption_status": "unadopted",
                    "adoption_error": None,
                    "discovery_status": "complete",
                    "discovery_error": None,
                }
            )

        with mock.patch.object(
            project_journal,
            "MAX_DISCOVERY_ROLLOUT_ASSOCIATIONS",
            3,
        ):
            with mock.patch.object(
                project_journal,
                "_repo_root_for_path",
                side_effect=resolve_candidate,
            ):
                with mock.patch.object(
                    project_journal,
                    "_enrich_discovered_repo",
                    side_effect=enrich_candidate,
                ):
                    rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(int(row["rollout_count"]) for row in rows), 3)
        anchors = {
            row["discovery_error"]["discovery_coverage"]["coverage_id"]: (
                row["discovery_error"]["discovery_coverage"]
            )
            for row in rows
            if isinstance(row.get("discovery_error"), dict)
            and "discovery_coverage" in row["discovery_error"]
        }
        self.assertEqual(len(anchors), 1)
        for row in rows:
            self.assertEqual(row["coverage_status"], "partial")
            coverage_id = row["discovery_coverage_ref"]["coverage_id"]
            coverage = anchors[coverage_id]
            self.assertEqual(coverage["code"], "discovery_limit_exceeded")
            self.assertEqual(
                coverage["limit_name"],
                "retained rollout association count",
            )
            self.assertEqual(coverage["limit"], 3)
            self.assertEqual(coverage["observed"], 4)
            self.assertEqual(coverage["rollout_associations_counted"], 4)
        unresolved = next(row for row in rows if row["repo"] is None)
        self.assertIn("repo_resolution", unresolved["discovery_error"])
        self.assertNotIn("discovery_coverage", unresolved["discovery_error"])

    def test_discover_repos_shares_rollout_cap_across_active_archive(self) -> None:
        repo = self.init_repo().resolve()
        codex_home = self.root / "codex-home"
        active = codex_home / "sessions/2026/05/05"
        archive = codex_home / "archived_sessions"
        active.mkdir(parents=True)
        archive.mkdir(parents=True)
        payload = json.dumps({"payload": {"cwd": str(repo)}}) + "\n"
        (active / "rollout-active.jsonl").write_text(payload, encoding="utf-8")
        (archive / "rollout-2026-05-06T10-00-00-archive.jsonl").write_text(
            payload,
            encoding="utf-8",
        )

        with mock.patch.object(project_journal, "MAX_DISCOVERY_ROLLOUTS", 1):
            rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["rollout_count"], 1)
        self.assertEqual(rows[0]["coverage_status"], "partial")
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["limit_name"], "rollout count")
        self.assertEqual(coverage["observed"], 2)

    def test_discover_repos_shares_byte_and_record_caps_across_sources(
        self,
    ) -> None:
        repo = self.init_repo().resolve()
        codex_home = self.root / "codex-home"
        active = codex_home / "sessions/2026/05/05"
        archive = codex_home / "archived_sessions"
        active.mkdir(parents=True)
        archive.mkdir(parents=True)
        payload = (json.dumps({"payload": {"cwd": str(repo)}}) + "\n").encode()
        (active / "rollout-active.jsonl").write_bytes(payload)
        (archive / "rollout-2026-05-06T10-00-00-archive.jsonl").write_bytes(payload)

        for constant, limit, limit_name, observed in (
            (
                "MAX_DISCOVERY_TOTAL_BYTES",
                len(payload),
                "total rollout bytes",
                len(payload) * 2,
            ),
            ("MAX_DISCOVERY_RECORDS", 1, "rollout record count", 2),
        ):
            with self.subTest(constant=constant):
                with mock.patch.object(project_journal, constant, limit):
                    rows = project_journal._discover_repos(codex_home, 9999)

                self.assertEqual(rows[0]["rollout_count"], 1)
                self.assertEqual(rows[0]["coverage_status"], "partial")
                coverage = rows[0]["discovery_error"]["discovery_coverage"]
                self.assertEqual(coverage["limit_name"], limit_name)
                self.assertEqual(coverage["observed"], observed)

    def test_discover_repos_shares_deadline_across_active_archive(self) -> None:
        repo = self.init_repo().resolve()
        codex_home = self.root / "codex-home"
        active = codex_home / "sessions/2026/05/05/rollout-active.jsonl"
        active.parent.mkdir(parents=True)
        active.write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        clock = {"now": 100.0}
        state_ids: list[int] = []

        def iter_rollouts(
            root: pathlib.Path,
            state: project_journal._DiscoveryScanState,
            *,
            recurse_directories: bool,
        ) -> object:
            self.assertEqual(
                recurse_directories,
                root.name == "sessions",
            )
            state_ids.append(id(state))
            if root.name == "sessions":
                yield active
                clock["now"] = 100.75
                return
            clock["now"] = 101.0
            state.check_deadline()

        def enrich_candidate(
            root: pathlib.Path,
            row: dict[str, object],
            script: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> None:
            del root, script, deadline
            row.update(
                {
                    "adoption_status": "unadopted",
                    "adoption_error": None,
                    "discovery_status": "complete",
                    "discovery_error": None,
                }
            )

        with mock.patch.object(project_journal, "DISCOVERY_TIMEOUT_SECONDS", 1.0):
            with mock.patch.object(
                project_journal.time,
                "monotonic",
                side_effect=lambda: clock["now"],
            ):
                with mock.patch.object(
                    project_journal,
                    "_iter_rollout_paths",
                    side_effect=iter_rollouts,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_repo_root_for_path",
                        return_value=repo,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_enrich_discovered_repo",
                            side_effect=enrich_candidate,
                        ):
                            rows = project_journal._discover_repos(
                                codex_home,
                                9999,
                            )

        self.assertEqual(len(set(state_ids)), 1)
        self.assertEqual(rows[0]["rollout_count"], 1)
        self.assertEqual(rows[0]["coverage_status"], "partial")
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["code"], "discovery_deadline_exceeded")
        self.assertEqual(
            pathlib.Path(coverage["source"]).name,
            "archived_sessions",
        )

    def test_discover_repos_keeps_archive_when_active_scan_fails(self) -> None:
        repo = self.init_repo().resolve()
        codex_home = self.root / "codex-home"
        archive = codex_home / "archived_sessions"
        archive.mkdir(parents=True)
        archived_rollout = archive / "rollout-2026-05-06T10-00-00-archive.jsonl"
        archived_rollout.write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )

        def iter_rollouts(
            root: pathlib.Path,
            state: project_journal._DiscoveryScanState,
            *,
            recurse_directories: bool,
        ) -> object:
            del state
            self.assertEqual(
                recurse_directories,
                root.name == "sessions",
            )
            if root.name == "sessions":
                raise PermissionError(
                    errno.EACCES,
                    "injected active session scan failure",
                    str(root),
                )
            yield archived_rollout

        with mock.patch.object(
            project_journal,
            "_iter_rollout_paths",
            side_effect=iter_rollouts,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 1)
        self.assertEqual(pathlib.Path(rows[0]["repo"]), repo)
        self.assertEqual(rows[0]["rollout_count"], 1)
        self.assertEqual(rows[0]["coverage_status"], "partial")
        coverage = rows[0]["discovery_error"]["discovery_coverage"]
        self.assertEqual(coverage["errno"], errno.EACCES)
        self.assertEqual(pathlib.Path(coverage["source"]).name, "sessions")

    def test_discover_repos_reads_synthetic_rollouts(self) -> None:
        repo = self.init_repo()
        nested = repo / "nested"
        nested.mkdir()
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Alpha Work",
            status="active",
            updated="2026-05-05",
        )
        invalid = self.root / "not-a-repo"
        invalid.mkdir()

        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-test.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(nested)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(repo)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(invalid)}})
            + "\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(pathlib.Path(rows[0]["repo"]), repo.resolve())
        self.assertTrue(rows[0]["has_journal_dir"])
        self.assertEqual(rows[0]["journal_count"], 1)
        self.assertEqual(rows[0]["adoption_status"], "unadopted")
        self.assertIsNone(rows[0]["adoption_error"])
        self.assertFalse(rows[0]["tracked_journal_adopted"])
        self.assertEqual(rows[0]["valid_tracked_journal_count"], 0)
        self.assertEqual(rows[0]["rollout_count"], 1)
        self.assertFalse(rows[0]["hooks_installed"])

    def test_discover_repos_reports_unresolved_hook_config_as_auxiliary_error(
        self,
    ) -> None:
        repo = self.init_repo()
        journal = self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Alpha Work",
            status="active",
            updated="2026-05-05",
        )
        added = run_git(
            repo,
            "add",
            "--",
            str(journal.relative_to(repo)),
        )
        self.assertEqual(added.returncode, 0, added.stderr)

        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-unresolved-hook-config.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        env = os.environ.copy()
        env["HOME"] = str(self.home)
        for name in (
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_SYSTEM",
            "XDG_CONFIG_HOME",
        ):
            env.pop(name, None)

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "discover-repos",
                "--codex-home",
                str(codex_home),
                "--since-days",
                "9999",
                "--json",
            ],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["adoption_status"], "adopted")
        self.assertIsNone(row["adoption_error"])
        self.assertTrue(row["tracked_journal_adopted"])
        self.assertEqual(row["valid_tracked_journal_count"], 1)
        self.assertEqual(row["journal_count"], 1)
        self.assertFalse(row["index_ignored"])
        self.assertIsNone(row["hooks_installed"])
        self.assertEqual(row["discovery_status"], "inconclusive")
        self.assertEqual(set(row["discovery_error"]), {"hooks_installed"})
        hook_error = row["discovery_error"]["hooks_installed"]
        self.assertEqual(
            hook_error["code"],
            "discovery_auxiliary_inspection_failed",
        )
        self.assertIn(
            "implicit system Git config path cannot be proved safely",
            hook_error["message"],
        )

    def test_enrich_discovered_repo_isolates_exclude_failure(self) -> None:
        repo = self.init_repo().resolve()
        configured = run_git(
            repo,
            "config",
            "--local",
            "core.hooksPath",
            ".githooks",
        )
        self.assertEqual(configured.returncode, 0, configured.stderr)
        row: dict[str, object] = {"repo": str(repo)}

        with mock.patch.object(
            project_journal,
            "_is_excluded",
            side_effect=project_journal.UserError("injected exclude lookup failure"),
        ):
            project_journal._enrich_discovered_repo(repo, row, SCRIPT)

        self.assertEqual(row["adoption_status"], "unadopted")
        self.assertIsNone(row["adoption_error"])
        self.assertEqual(row["journal_count"], 0)
        self.assertIsNone(row["index_ignored"])
        self.assertFalse(row["hooks_installed"])
        self.assertEqual(row["discovery_status"], "inconclusive")
        discovery_error = row["discovery_error"]
        self.assertIsInstance(discovery_error, dict)
        assert isinstance(discovery_error, dict)
        self.assertEqual(set(discovery_error), {"index_ignored"})
        self.assertIn(
            "injected exclude lookup failure",
            discovery_error["index_ignored"]["message"],
        )

    def test_enrich_rejects_journal_fifo_without_blocking(self) -> None:
        repo = self.init_repo().resolve()
        journal = repo / "docs/project_journal/entry.md"
        journal.parent.mkdir(parents=True)
        os.mkfifo(journal)
        row: dict[str, object] = {"repo": str(repo)}
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }
        started = time.monotonic()

        with mock.patch.object(
            project_journal,
            "_discover_adoption_status",
            return_value=adoption,
        ):
            with mock.patch.object(
                project_journal,
                "_is_excluded",
                return_value=False,
            ):
                with mock.patch.object(
                    project_journal,
                    "_has_hook_marker",
                    return_value=False,
                ):
                    project_journal._enrich_discovered_repo(
                        repo,
                        row,
                        SCRIPT,
                        deadline=started + 1.0,
                    )

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIsNone(row["journal_count"])
        error = row["discovery_error"]["journal_count"]
        self.assertEqual(error["code"], "generated_index_inspection_failed")
        self.assertEqual(
            error["errno"],
            getattr(errno, "ENXIO", errno.EINVAL),
        )

    def test_enrich_rejects_exclude_fifo_without_blocking(self) -> None:
        repo = self.init_repo().resolve()
        exclude = repo / "exclude-fifo"
        os.mkfifo(exclude)
        row: dict[str, object] = {"repo": str(repo)}
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }
        started = time.monotonic()

        with mock.patch.object(
            project_journal,
            "_discover_adoption_status",
            return_value=adoption,
        ):
            with mock.patch.object(
                project_journal,
                "_journal_paths",
                return_value=[],
            ):
                with mock.patch.object(
                    project_journal,
                    "_git_path",
                    return_value=exclude,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_has_hook_marker",
                        return_value=False,
                    ):
                        project_journal._enrich_discovered_repo(
                            repo,
                            row,
                            SCRIPT,
                            deadline=started + 1.0,
                        )

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIsNone(row["index_ignored"])
        error = row["discovery_error"]["index_ignored"]
        self.assertEqual(
            error["code"],
            "discovery_auxiliary_inspection_failed",
        )
        self.assertEqual(
            error["errno"],
            getattr(errno, "ENXIO", errno.EINVAL),
        )

    def test_enrich_rejects_hook_fifo_without_blocking(self) -> None:
        repo = self.init_repo().resolve()
        hook_dir = repo / "hook-fifo"
        hook_dir.mkdir()
        os.mkfifo(hook_dir / "post-merge")
        row: dict[str, object] = {"repo": str(repo)}
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }
        started = time.monotonic()

        with mock.patch.object(
            project_journal,
            "_discover_adoption_status",
            return_value=adoption,
        ):
            with mock.patch.object(
                project_journal,
                "_journal_paths",
                return_value=[],
            ):
                with mock.patch.object(
                    project_journal,
                    "_is_excluded",
                    return_value=False,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_hook_path_plan",
                        return_value=project_journal._HookPathPlan(
                            root=repo,
                            components=("hook-fifo",),
                        ),
                    ):
                        project_journal._enrich_discovered_repo(
                            repo,
                            row,
                            SCRIPT,
                            deadline=started + 1.0,
                        )

        self.assertLess(time.monotonic() - started, 1.0)
        self.assertIsNone(row["hooks_installed"])
        error = row["discovery_error"]["hooks_installed"]
        self.assertEqual(
            error["code"],
            "discovery_auxiliary_inspection_failed",
        )
        self.assertEqual(
            error["errno"],
            getattr(errno, "ENXIO", errno.EINVAL),
        )

    def test_discovery_auxiliary_reader_rejects_unsafe_files(self) -> None:
        target = self.root / "target"
        target.write_text("target\n", encoding="utf-8")
        symlink = self.root / "symlink"
        symlink.symlink_to(target)
        directory = self.root / "directory"
        directory.mkdir()
        oversized = self.root / "oversized"
        oversized.write_bytes(b"x" * 5)

        for name, path, byte_limit, expected_code in (
            (
                "symlink",
                symlink,
                16,
                "discovery_auxiliary_inspection_failed",
            ),
            (
                "directory",
                directory,
                16,
                "discovery_auxiliary_inspection_failed",
            ),
            (
                "oversized",
                oversized,
                4,
                "discovery_auxiliary_inspection_limit_exceeded",
            ),
        ):
            with self.subTest(name=name):
                with self.assertRaises(
                    project_journal.DiscoveryAuxiliaryInspectionError
                ) as raised:
                    project_journal._read_discovery_regular_path(
                        path,
                        label="test auxiliary",
                        byte_limit=byte_limit,
                        deadline=time.monotonic() + 1.0,
                    )
                self.assertEqual(raised.exception.code, expected_code)
                if name == "oversized":
                    self.assertEqual(raised.exception.limit, 4)
                    self.assertEqual(raised.exception.observed, 5)

    def test_discovery_auxiliary_open_preserves_primary_over_close_failure(
        self,
    ) -> None:
        auxiliary = self.root / "auxiliary-open-primary-close"
        auxiliary.write_text("content\n", encoding="utf-8")
        primary = project_journal.DiscoveryAuxiliaryInspectionError(
            "injected binding failure",
            error_number=errno.EACCES,
        )
        close_error = OSError(
            errno.EIO,
            "injected binding close failure",
        )
        actual_close = project_journal.os.close

        def close_then_fail(fd: int) -> None:
            actual_close(fd)
            raise close_error

        with (
            mock.patch.object(
                project_journal.os,
                "fstat",
                side_effect=primary,
            ),
            mock.patch.object(
                project_journal.os,
                "close",
                side_effect=close_then_fail,
            ),
            self.assertRaises(
                project_journal.DiscoveryAuxiliaryInspectionError
            ) as raised,
        ):
            project_journal._open_discovery_regular_path(
                auxiliary,
                label="test auxiliary",
                deadline=time.monotonic() + 1.0,
                missing_ok=False,
            )

        self.assertIs(raised.exception, primary)
        self.assertEqual(primary.errno, errno.EACCES)
        notes = "\n".join(getattr(primary, "__notes__", ()))
        self.assertIn("test auxiliary binding descriptor cleanup failed", notes)
        self.assertIn("errno=5 (EIO)", notes)
        self.assertIn("injected binding close failure", notes)

    def test_discovery_auxiliary_read_preserves_primary_over_close_failure(
        self,
    ) -> None:
        auxiliary = self.root / "auxiliary-read-primary-close"
        auxiliary.write_text("content\n", encoding="utf-8")
        primary = project_journal.DiscoveryAuxiliaryInspectionLimitExceeded(
            "injected read limit",
            limit_name="test auxiliary bytes",
            limit=1,
            observed=2,
        )
        close_error = OSError(
            errno.EIO,
            "injected read close failure",
        )
        actual_close = project_journal.os.close

        def close_then_fail(fd: int) -> None:
            actual_close(fd)
            raise close_error

        with (
            mock.patch.object(
                project_journal.os,
                "lseek",
                side_effect=primary,
            ),
            mock.patch.object(
                project_journal.os,
                "close",
                side_effect=close_then_fail,
            ),
            self.assertRaises(
                project_journal.DiscoveryAuxiliaryInspectionLimitExceeded
            ) as raised,
        ):
            project_journal._read_discovery_regular_path(
                auxiliary,
                label="test auxiliary",
                byte_limit=1024,
                deadline=time.monotonic() + 1.0,
            )

        self.assertIs(raised.exception, primary)
        self.assertEqual(primary.limit_name, "test auxiliary bytes")
        self.assertEqual(primary.limit, 1)
        self.assertEqual(primary.observed, 2)
        notes = "\n".join(getattr(primary, "__notes__", ()))
        self.assertIn("test auxiliary descriptor cleanup failed", notes)
        self.assertIn("errno=5 (EIO)", notes)
        self.assertIn("injected read close failure", notes)

    def test_discovery_auxiliary_close_only_failures_are_structured(
        self,
    ) -> None:
        auxiliary = self.root / "auxiliary-close-only"
        auxiliary.write_text("content\n", encoding="utf-8")

        for operation in ("read", "exists"):
            with self.subTest(operation=operation):
                close_error = OSError(
                    errno.EIO,
                    f"injected {operation} close-only failure",
                )
                actual_close = project_journal.os.close

                def close_then_fail(fd: int) -> None:
                    actual_close(fd)
                    raise close_error

                with mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=close_then_fail,
                ):
                    with self.assertRaises(
                        project_journal.DiscoveryAuxiliaryInspectionError
                    ) as raised:
                        if operation == "read":
                            project_journal._read_discovery_regular_path(
                                auxiliary,
                                label="test auxiliary",
                                byte_limit=1024,
                                deadline=time.monotonic() + 1.0,
                            )
                        else:
                            project_journal._discovery_regular_path_exists(
                                auxiliary,
                                label="test auxiliary",
                                deadline=time.monotonic() + 1.0,
                            )

                self.assertIs(raised.exception.__cause__, close_error)
                self.assertEqual(
                    raised.exception.code,
                    "discovery_auxiliary_inspection_failed",
                )
                self.assertEqual(raised.exception.errno, errno.EIO)
                self.assertIn(
                    "test auxiliary",
                    str(raised.exception),
                )
                self.assertIn(
                    "descriptor cleanup failed",
                    str(raised.exception),
                )

    def test_discovery_auxiliary_read_does_not_consume_ambient_exception(
        self,
    ) -> None:
        auxiliary = self.root / "auxiliary-read-ambient-close"
        auxiliary.write_text("content\n", encoding="utf-8")
        ambient = RuntimeError("unrelated outer exception")
        close_error = OSError(
            errno.EIO,
            "injected read ambient close failure",
        )
        actual_close = project_journal.os.close

        def close_then_fail(fd: int) -> None:
            actual_close(fd)
            raise close_error

        try:
            raise ambient
        except RuntimeError:
            with (
                mock.patch.object(
                    project_journal.os,
                    "close",
                    side_effect=close_then_fail,
                ),
                self.assertRaises(
                    project_journal.DiscoveryAuxiliaryInspectionError
                ) as raised,
            ):
                project_journal._read_discovery_regular_path(
                    auxiliary,
                    label="test auxiliary",
                    byte_limit=1024,
                    deadline=time.monotonic() + 1.0,
                )

        self.assertIs(raised.exception.__cause__, close_error)
        self.assertEqual(
            raised.exception.code,
            "discovery_auxiliary_inspection_failed",
        )
        self.assertEqual(raised.exception.errno, errno.EIO)
        self.assertEqual(getattr(ambient, "__notes__", ()), ())

    def test_discovery_auxiliary_close_only_base_exception_remains_exact(
        self,
    ) -> None:
        auxiliary = self.root / "auxiliary-close-only-base-exception"
        auxiliary.write_text("content\n", encoding="utf-8")
        close_error = LegacyInterrupt("injected close-only interruption")
        actual_close = project_journal.os.close

        def close_then_interrupt(fd: int) -> None:
            actual_close(fd)
            raise close_error

        raised_error: LegacyInterrupt | None = None
        with mock.patch.object(
            project_journal.os,
            "close",
            side_effect=close_then_interrupt,
        ):
            try:
                project_journal._read_discovery_regular_path(
                    auxiliary,
                    label="test auxiliary",
                    byte_limit=1024,
                    deadline=time.monotonic() + 1.0,
                )
            except LegacyInterrupt as raised:
                raised_error = raised
            else:
                self.fail("expected close-only interruption")

        self.assertIs(raised_error, close_error)
        notes = "\n".join(getattr(close_error, "__notes__", ()))
        self.assertIn("test auxiliary descriptor cleanup failed", notes)
        self.assertIn("type=LegacyInterrupt", notes)

    def test_enrich_reports_oversized_exclude_and_hook_files(self) -> None:
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }
        for field in ("index_ignored", "hooks_installed"):
            with self.subTest(field=field):
                repo = self.init_repo(f"oversized-{field}").resolve()
                auxiliary_dir = repo / "auxiliary"
                auxiliary_dir.mkdir()
                auxiliary = (
                    auxiliary_dir / "exclude"
                    if field == "index_ignored"
                    else auxiliary_dir / "post-merge"
                )
                auxiliary.write_bytes(b"x" * 5)
                row: dict[str, object] = {"repo": str(repo)}

                with mock.patch.object(
                    project_journal,
                    "_discover_adoption_status",
                    return_value=adoption,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_journal_paths",
                        return_value=[],
                    ):
                        if field == "index_ignored":
                            with mock.patch.object(
                                project_journal,
                                "_git_path",
                                return_value=auxiliary,
                            ):
                                with mock.patch.object(
                                    project_journal,
                                    "_has_hook_marker",
                                    return_value=False,
                                ):
                                    with mock.patch.object(
                                        project_journal,
                                        "MAX_DISCOVERY_EXCLUDE_BYTES",
                                        4,
                                    ):
                                        project_journal._enrich_discovered_repo(
                                            repo,
                                            row,
                                            SCRIPT,
                                        )
                        else:
                            with mock.patch.object(
                                project_journal,
                                "_is_excluded",
                                return_value=False,
                            ):
                                with mock.patch.object(
                                    project_journal,
                                    "_hook_path_plan",
                                    return_value=project_journal._HookPathPlan(
                                        root=repo,
                                        components=("auxiliary",),
                                    ),
                                ):
                                    with mock.patch.object(
                                        project_journal,
                                        "MAX_DISCOVERY_HOOK_BYTES",
                                        4,
                                    ):
                                        project_journal._enrich_discovered_repo(
                                            repo,
                                            row,
                                            SCRIPT,
                                        )

                self.assertIsNone(row[field])
                error = row["discovery_error"][field]
                self.assertEqual(
                    error["code"],
                    "discovery_auxiliary_inspection_limit_exceeded",
                )
                self.assertEqual(error["limit"], 4)
                self.assertEqual(error["observed"], 5)

    def test_is_excluded_bounds_slow_git_to_candidate_deadline(self) -> None:
        repo = self.init_repo().resolve()
        runtime = self.make_fake_git_runtime(
            "slow-discovery-git",
            source_text="#!/bin/sh\nsleep 30\n",
        )
        started = time.monotonic()
        try:
            with mock.patch.object(project_journal, "_GIT_RUNTIME", runtime):
                with mock.patch.object(
                    project_journal,
                    "_GIT_RUNTIME_ERROR",
                    None,
                ):
                    with self.assertRaisesRegex(
                        project_journal.DiscoveryAuxiliaryInspectionError,
                        "candidate deadline",
                    ):
                        project_journal._is_excluded(
                            repo,
                            project_journal.DEFAULT_INDEX.as_posix(),
                            deadline=started + 0.15,
                        )
        finally:
            runtime.snapshot_owner.cleanup()

        self.assertLess(time.monotonic() - started, 1.5)

    def test_enrich_forwards_one_absolute_deadline_to_auxiliary_probes(
        self,
    ) -> None:
        repo = self.init_repo().resolve()
        row: dict[str, object] = {"repo": str(repo)}
        deadline = time.monotonic() + 5.0
        observed: list[float | None] = []
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }

        def record_adoption(
            root: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> dict[str, object]:
            del root
            observed.append(deadline)
            return adoption

        def record_stat(
            path: pathlib.Path,
            *,
            follow_symlinks: bool = True,
            deadline: float | None = None,
            deadline_error: str,
        ) -> None:
            del path, follow_symlinks, deadline_error
            observed.append(deadline)
            return None

        def record_paths(
            root: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> list[pathlib.Path]:
            del root
            observed.append(deadline)
            return []

        def record_boolean(
            root: pathlib.Path,
            *args: str,
            deadline: float | None = None,
            **kwargs: object,
        ) -> bool:
            del root, args, kwargs
            observed.append(deadline)
            return False

        with mock.patch.object(
            project_journal,
            "_discover_adoption_status",
            side_effect=record_adoption,
        ):
            with mock.patch.object(
                project_journal,
                "_stat_path_if_present",
                side_effect=record_stat,
            ):
                with mock.patch.object(
                    project_journal,
                    "_journal_paths",
                    side_effect=record_paths,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_is_excluded",
                        side_effect=record_boolean,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_has_hook_marker",
                            side_effect=record_boolean,
                        ):
                            with mock.patch.object(
                                project_journal,
                                "_discovery_regular_path_exists",
                                side_effect=record_boolean,
                            ):
                                project_journal._enrich_discovered_repo(
                                    repo,
                                    row,
                                    SCRIPT,
                                    deadline=deadline,
                                )

        self.assertTrue(observed)
        self.assertEqual(set(observed), {deadline})

    def test_enrich_missing_auxiliary_paths_are_authoritative_negatives(
        self,
    ) -> None:
        repo = self.init_repo().resolve()
        row: dict[str, object] = {"repo": str(repo)}
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }
        missing_exclude = repo / "missing-exclude"
        with mock.patch.object(
            project_journal,
            "_discover_adoption_status",
            return_value=adoption,
        ):
            with mock.patch.object(
                project_journal,
                "_git_path",
                return_value=missing_exclude,
            ):
                with mock.patch.object(
                    project_journal,
                    "_hook_path_plan",
                    return_value=project_journal._HookPathPlan(
                        root=repo,
                        components=("missing-hooks",),
                    ),
                ):
                    project_journal._enrich_discovered_repo(repo, row, SCRIPT)

        self.assertFalse(row["has_journal_dir"])
        self.assertEqual(row["journal_count"], 0)
        self.assertFalse(row["has_index"])
        self.assertFalse(row["index_ignored"])
        self.assertFalse(row["hooks_installed"])
        self.assertEqual(row["discovery_status"], "complete")
        self.assertIsNone(row["discovery_error"])

    def test_enrich_reports_inaccessible_journal_root_fields(self) -> None:
        repo = self.init_repo().resolve()
        journal_root = repo / project_journal.JOURNAL_ROOT
        row: dict[str, object] = {"repo": str(repo)}
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }
        actual_stat = project_journal.os.stat

        def inaccessible_root(
            path: os.PathLike[str] | str | int,
            *args: object,
            **kwargs: object,
        ) -> os.stat_result:
            if not isinstance(path, int) and pathlib.Path(path) == journal_root:
                raise PermissionError(
                    errno.EACCES,
                    "injected inaccessible journal root",
                    str(path),
                )
            return actual_stat(path, *args, **kwargs)

        with mock.patch.object(
            project_journal,
            "_discover_adoption_status",
            return_value=adoption,
        ):
            with mock.patch.object(
                project_journal,
                "_is_excluded",
                return_value=False,
            ):
                with mock.patch.object(
                    project_journal,
                    "_has_hook_marker",
                    return_value=False,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "stat",
                        side_effect=inaccessible_root,
                    ):
                        project_journal._enrich_discovered_repo(repo, row, SCRIPT)

        self.assertIsNone(row["has_journal_dir"])
        self.assertIsNone(row["journal_count"])
        self.assertFalse(row["has_index"])
        self.assertFalse(row["index_ignored"])
        self.assertFalse(row["hooks_installed"])
        self.assertEqual(row["discovery_status"], "inconclusive")
        errors = row["discovery_error"]
        self.assertIsInstance(errors, dict)
        assert isinstance(errors, dict)
        self.assertEqual(set(errors), {"has_journal_dir", "journal_count"})
        for field in errors:
            self.assertEqual(errors[field]["errno"], errno.EACCES)
            self.assertEqual(errors[field]["error_name"], "EACCES")

    def test_enrich_reports_journal_scan_io_failure_only_for_count(self) -> None:
        repo = self.init_repo().resolve()
        journal_root = repo / project_journal.JOURNAL_ROOT
        journal_root.mkdir(parents=True)
        row: dict[str, object] = {"repo": str(repo)}
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }
        actual_scandir = project_journal.os.scandir

        def unreadable_directory(
            path: os.PathLike[str] | str,
        ) -> os.ScandirIterator[str]:
            if pathlib.Path(path) == journal_root:
                raise OSError(
                    errno.EIO,
                    "injected journal directory scan failure",
                    str(path),
                )
            return actual_scandir(path)

        with mock.patch.object(
            project_journal,
            "_discover_adoption_status",
            return_value=adoption,
        ):
            with mock.patch.object(
                project_journal,
                "_is_excluded",
                return_value=False,
            ):
                with mock.patch.object(
                    project_journal,
                    "_has_hook_marker",
                    return_value=False,
                ):
                    with mock.patch.object(
                        project_journal.os,
                        "scandir",
                        side_effect=unreadable_directory,
                    ):
                        project_journal._enrich_discovered_repo(repo, row, SCRIPT)

        self.assertTrue(row["has_journal_dir"])
        self.assertIsNone(row["journal_count"])
        self.assertFalse(row["has_index"])
        self.assertFalse(row["index_ignored"])
        self.assertFalse(row["hooks_installed"])
        self.assertEqual(row["discovery_status"], "inconclusive")
        errors = row["discovery_error"]
        self.assertIsInstance(errors, dict)
        assert isinstance(errors, dict)
        self.assertEqual(set(errors), {"journal_count"})
        self.assertEqual(errors["journal_count"]["errno"], errno.EIO)
        self.assertEqual(errors["journal_count"]["error_name"], "EIO")

    def test_enrich_reports_generated_marker_read_failures(self) -> None:
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }
        actual_open = project_journal.os.open

        for error_number in (errno.EACCES, errno.EIO):
            with self.subTest(error_number=error_number):
                repo = self.init_repo(f"marker-error-{error_number}").resolve()
                journal = repo / "docs/project_journal/entry.md"
                journal.parent.mkdir(parents=True)
                journal.write_text("---\n", encoding="utf-8")
                row: dict[str, object] = {"repo": str(repo)}

                def fail_marker_open(
                    path: os.PathLike[str] | str,
                    *args: object,
                    **kwargs: object,
                ) -> int:
                    if pathlib.Path(path) == journal:
                        raise OSError(
                            error_number,
                            "injected marker read failure",
                            str(path),
                        )
                    return actual_open(path, *args, **kwargs)

                with mock.patch.object(
                    project_journal,
                    "_discover_adoption_status",
                    return_value=adoption,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_is_excluded",
                        return_value=False,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_has_hook_marker",
                            return_value=False,
                        ):
                            with mock.patch.object(
                                project_journal.os,
                                "open",
                                side_effect=fail_marker_open,
                            ):
                                project_journal._enrich_discovered_repo(
                                    repo,
                                    row,
                                    SCRIPT,
                                )

                self.assertIsNone(row["journal_count"])
                self.assertEqual(row["discovery_status"], "inconclusive")
                error = row["discovery_error"]["journal_count"]
                self.assertEqual(
                    error["code"],
                    "generated_index_inspection_failed",
                )
                self.assertEqual(error["errno"], error_number)
                self.assertEqual(
                    error["error_name"],
                    errno.errorcode[error_number],
                )

    def test_enrich_reports_non_file_journal_marker_entries(self) -> None:
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }

        for kind, expected_code, expected_errno in (
            ("dangling", "generated_index_inspection_failed", errno.ELOOP),
            ("directory", "generated_index_inspection_failed", errno.EISDIR),
            (
                "oversized",
                "generated_index_inspection_limit_exceeded",
                None,
            ),
        ):
            with self.subTest(kind=kind):
                repo = self.init_repo(f"marker-entry-{kind}").resolve()
                journal = repo / "docs/project_journal/entry.md"
                journal.parent.mkdir(parents=True)
                if kind == "dangling":
                    journal.symlink_to(repo / "missing-target")
                elif kind == "directory":
                    journal.mkdir()
                else:
                    journal.write_bytes(
                        b"# Project Journal Index\n\n"
                        + b"x"
                        * (project_journal.MAX_GENERATED_INDEX_MARKER_LINE_BYTES + 1)
                    )
                row: dict[str, object] = {"repo": str(repo)}

                with mock.patch.object(
                    project_journal,
                    "_discover_adoption_status",
                    return_value=adoption,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_is_excluded",
                        return_value=False,
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_has_hook_marker",
                            return_value=False,
                        ):
                            project_journal._enrich_discovered_repo(
                                repo,
                                row,
                                SCRIPT,
                            )

                self.assertIsNone(row["journal_count"])
                self.assertEqual(row["discovery_status"], "inconclusive")
                error = row["discovery_error"]["journal_count"]
                self.assertEqual(error["code"], expected_code)
                if expected_errno is None:
                    self.assertNotIn("errno", error)
                    self.assertEqual(
                        error["limit_name"],
                        "generated-index marker line bytes",
                    )
                    self.assertEqual(
                        error["limit"],
                        project_journal.MAX_GENERATED_INDEX_MARKER_LINE_BYTES,
                    )
                else:
                    self.assertEqual(error["errno"], expected_errno)

    def test_enrich_isolates_inaccessible_index_exclude_and_hook_paths(
        self,
    ) -> None:
        repo = self.init_repo().resolve()
        journal_root = repo / project_journal.JOURNAL_ROOT
        journal_root.mkdir(parents=True)
        index_path = repo / project_journal.DEFAULT_INDEX
        exclude_path = repo / "blocked-exclude"
        hook_dir = repo / "blocked-hooks"
        hook_dir.mkdir()
        hook_path = hook_dir / "post-merge"
        adoption = {
            "adoption_status": "unadopted",
            "adoption_error": None,
            "tracked_journal_adopted": False,
            "tracked_non_generated_journal_count": 0,
            "valid_tracked_journal_count": 0,
        }
        actual_open = project_journal.os.open

        for field, blocked_path, error_number in (
            ("has_index", index_path, errno.EIO),
            ("index_ignored", exclude_path, errno.EACCES),
            ("hooks_installed", hook_path, errno.EIO),
        ):
            with self.subTest(field=field):
                row: dict[str, object] = {"repo": str(repo)}
                blocked_path.write_text("blocked\n", encoding="utf-8")

                def inaccessible_path(
                    path: os.PathLike[str] | str,
                    *args: object,
                    **kwargs: object,
                ) -> int:
                    if pathlib.Path(path) == blocked_path:
                        raise OSError(
                            error_number,
                            f"injected inaccessible {field}",
                            str(path),
                        )
                    return actual_open(path, *args, **kwargs)

                with mock.patch.object(
                    project_journal,
                    "_discover_adoption_status",
                    return_value=adoption,
                ):
                    with mock.patch.object(
                        project_journal, "_journal_paths", return_value=[]
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_git_path",
                            return_value=exclude_path,
                        ):
                            with mock.patch.object(
                                project_journal,
                                "_hook_path_plan",
                                return_value=project_journal._HookPathPlan(
                                    root=repo,
                                    components=("blocked-hooks",),
                                ),
                            ):
                                with mock.patch.object(
                                    project_journal.os,
                                    "open",
                                    side_effect=inaccessible_path,
                                ):
                                    project_journal._enrich_discovered_repo(
                                        repo,
                                        row,
                                        SCRIPT,
                                    )
                blocked_path.unlink()

                self.assertEqual(row["journal_count"], 0)
                self.assertEqual(row["discovery_status"], "inconclusive")
                errors = row["discovery_error"]
                self.assertIsInstance(errors, dict)
                assert isinstance(errors, dict)
                self.assertEqual(set(errors), {field})
                self.assertIsNone(row[field])
                self.assertEqual(errors[field]["errno"], error_number)
                self.assertEqual(
                    errors[field]["error_name"],
                    errno.errorcode[error_number],
                )
                for healthy_field in {
                    "has_index",
                    "index_ignored",
                    "hooks_installed",
                } - {field}:
                    self.assertFalse(row[healthy_field])

    def test_discover_repos_keeps_healthy_rows_when_adoption_is_inconclusive(
        self,
    ) -> None:
        healthy = self.init_repo("healthy")
        healthy_journal = self.write_journal(
            healthy,
            "docs/project_journal/2026/05/2026-05-05-healthy-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Healthy",
            status="active",
            updated="2026-05-05",
        )
        add = run_git(
            healthy,
            "add",
            "--",
            str(healthy_journal.relative_to(healthy)),
        )
        self.assertEqual(add.returncode, 0, add.stderr)

        failing = self.init_repo("failing")
        missing_oid = "a" * 40
        missing_path = (
            "docs/project_journal/2026/05/2026-05-05-missing-object-d4e5f6.md"
        )
        staged = run_git(
            failing,
            "update-index",
            "--add",
            "--info-only",
            "--cacheinfo",
            f"100644,{missing_oid},{missing_path}",
        )
        self.assertEqual(staged.returncode, 0, staged.stderr)

        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-mixed.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(healthy)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(failing)}})
            + "\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {
            pathlib.Path(row["repo"]).name: row for row in json.loads(result.stdout)
        }
        self.assertEqual(set(rows), {"healthy", "failing"})
        self.assertEqual(rows["healthy"]["adoption_status"], "adopted")
        self.assertIsNone(rows["healthy"]["adoption_error"])
        self.assertTrue(rows["healthy"]["tracked_journal_adopted"])
        self.assertEqual(rows["healthy"]["valid_tracked_journal_count"], 1)
        self.assertEqual(rows["failing"]["adoption_status"], "inconclusive")
        self.assertEqual(
            rows["failing"]["adoption_error"]["code"],
            "adoption_check_failed",
        )
        self.assertIn(
            "index blob is missing",
            rows["failing"]["adoption_error"]["message"],
        )
        self.assertIsNone(rows["failing"]["tracked_journal_adopted"])
        self.assertIsNone(rows["failing"]["tracked_non_generated_journal_count"])
        self.assertIsNone(rows["failing"]["valid_tracked_journal_count"])
        for row in rows.values():
            if row["tracked_journal_adopted"] is False:
                self.assertEqual(row["adoption_status"], "unadopted")

    def test_discover_repos_reports_resolution_errors_and_keeps_healthy_rows(
        self,
    ) -> None:
        healthy = self.init_repo("healthy").resolve()
        stalled = self.root / "stalled"
        stalled.mkdir()
        unreadable = self.root / "unreadable"
        unreadable.mkdir()
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-resolution-timeout.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(stalled)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(unreadable)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(healthy)}})
            + "\n",
            encoding="utf-8",
        )

        def resolve_candidate(
            path_text: str,
            *,
            codex_home: pathlib.Path | None = None,
            deadline: float | None = None,
        ) -> pathlib.Path | None:
            del codex_home
            self.assertIsNotNone(deadline)
            if pathlib.Path(path_text) == stalled:
                raise project_journal.UserError(
                    "Git repository resolution timed out after 10 seconds"
                )
            if pathlib.Path(path_text) == unreadable:
                raise OSError(
                    errno.EACCES,
                    "injected repository resolution access failure",
                )
            return healthy

        with mock.patch.object(
            project_journal,
            "_repo_root_for_path",
            side_effect=resolve_candidate,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 3)
        healthy_row = next(row for row in rows if row["repo"] is not None)
        self.assertEqual(pathlib.Path(healthy_row["repo"]), healthy)
        self.assertNotIn(
            "repo_resolution",
            healthy_row["discovery_error"] or {},
        )
        failures = {
            pathlib.Path(row["candidate_cwd"]).name: row
            for row in rows
            if row["repo"] is None
        }
        self.assertEqual(set(failures), {"stalled", "unreadable"})
        for row in failures.values():
            self.assertEqual(row["discovery_status"], "inconclusive")
            self.assertEqual(row["adoption_status"], "inconclusive")
            self.assertIsNone(row["tracked_journal_adopted"])
            self.assertEqual(set(row["discovery_error"]), {"repo_resolution"})
            self.assertEqual(row["rollout_count"], 1)
        self.assertIn(
            "timed out",
            failures["stalled"]["discovery_error"]["repo_resolution"]["message"],
        )
        self.assertIn(
            "access failure",
            failures["unreadable"]["discovery_error"]["repo_resolution"]["message"],
        )

    def test_discover_repos_reports_failed_git_looking_candidate_as_unresolved(
        self,
    ) -> None:
        candidate_root = self.root / "git-looking"
        candidate = candidate_root / "nested" / "missing"
        (candidate_root / "nested").mkdir(parents=True)
        marker = candidate_root / ".git"
        marker.write_text(
            "gitdir: ../unavailable-private-git-dir\n",
            encoding="utf-8",
        )
        observed_marker = candidate_root / "nested" / ".." / ".git"
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-git-looking-failure.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(candidate)}}) + "\n",
            encoding="utf-8",
        )
        failure = subprocess.CompletedProcess(
            [],
            128,
            "",
            "fatal: injected Git resolution failure",
        )

        with mock.patch.object(
            project_journal,
            "_run_git",
            return_value=failure,
        ):
            rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertIsNone(row["repo"])
        self.assertEqual(row["candidate_cwd"], str(candidate))
        self.assertEqual(row["rollout_count"], 1)
        self.assertEqual(row["coverage_status"], "complete")
        self.assertEqual(row["discovery_status"], "inconclusive")
        self.assertEqual(row["adoption_status"], "inconclusive")
        self.assertEqual(
            row["adoption_error"]["code"],
            "repository_resolution_failed",
        )
        self.assertIsNone(row["tracked_journal_adopted"])
        self.assertIsNone(row["tracked_non_generated_journal_count"])
        self.assertIsNone(row["valid_tracked_journal_count"])
        resolution_error = row["discovery_error"]["repo_resolution"]
        self.assertEqual(
            resolution_error["code"],
            "repository_resolution_failed",
        )
        self.assertEqual(
            resolution_error["resolution_reason"],
            "git_marker_present",
        )
        self.assertEqual(resolution_error["marker_kind"], "regular_file")
        self.assertNotIn("marker_path", resolution_error)
        self.assertEqual(
            resolution_error["marker_path_hint"],
            str(observed_marker),
        )
        self.assertEqual(
            resolution_error["marker_path_status"],
            "path_unverified",
        )
        self.assertIn(
            "regular file descriptor-relative .git entry",
            resolution_error["message"],
        )
        self.assertIn(str(observed_marker), resolution_error["message"])

    def test_discover_repos_shares_resolution_budget_with_adoption(self) -> None:
        repo = self.init_repo("healthy").resolve()
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-shared-budget.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )
        observed: dict[str, float | None] = {}
        clock = {"now": 100.0}

        def resolve_candidate(
            path_text: str,
            *,
            codex_home: pathlib.Path | None = None,
            deadline: float | None = None,
        ) -> pathlib.Path | None:
            del path_text, codex_home
            observed["resolution"] = deadline
            clock["now"] = 106.0
            return repo

        def tracked_adoption(
            root: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> dict[str, object]:
            self.assertEqual(root, repo)
            observed["adoption"] = deadline
            return {
                "tracked_journal_adopted": False,
                "tracked_non_generated_journal_count": 0,
                "valid_tracked_journal_count": 0,
            }

        with mock.patch.object(
            project_journal.time,
            "monotonic",
            side_effect=lambda: clock["now"],
        ):
            with mock.patch.object(
                project_journal,
                "_repo_root_for_path",
                side_effect=resolve_candidate,
            ):
                with mock.patch.object(
                    project_journal,
                    "_tracked_journal_adoption",
                    side_effect=tracked_adoption,
                ):
                    with mock.patch.object(
                        project_journal,
                        "_journal_paths",
                        return_value=[],
                    ):
                        with mock.patch.object(
                            project_journal,
                            "_is_excluded",
                            return_value=False,
                        ):
                            with mock.patch.object(
                                project_journal,
                                "_has_hook_marker",
                                return_value=False,
                            ):
                                project_journal._discover_repos(codex_home, 9999)

        expected = 100.0 + project_journal.GIT_ADOPTION_VALIDATION_TIMEOUT_SECONDS
        self.assertEqual(observed, {"resolution": expected, "adoption": expected})

    def test_discover_repos_retries_inconclusive_with_later_fresh_budget(
        self,
    ) -> None:
        repo = self.init_repo("healthy").resolve()
        slow = self.root / "slow-candidate"
        fresh = self.root / "fresh-candidate"
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-fresh-retry.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(slow)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(fresh)}})
            + "\n",
            encoding="utf-8",
        )
        clock = {"now": 100.0}
        remaining_by_candidate = {
            str(slow): 0.5,
            str(fresh): 5.0,
        }
        enrichment_budgets: list[float] = []

        def resolve_candidate(
            path_text: str,
            *,
            codex_home: pathlib.Path | None = None,
            deadline: float | None = None,
        ) -> pathlib.Path:
            del codex_home
            self.assertIsNotNone(deadline)
            assert deadline is not None
            clock["now"] = deadline - remaining_by_candidate[path_text]
            return repo

        def enrich_candidate(
            root: pathlib.Path,
            row: dict[str, object],
            script: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> None:
            del script
            self.assertEqual(root, repo)
            self.assertIsNotNone(deadline)
            assert deadline is not None
            enrichment_budgets.append(deadline - clock["now"])
            if len(enrichment_budgets) == 1:
                row["adoption_status"] = "inconclusive"
                row["adoption_error"] = {"code": "adoption_check_failed"}
            else:
                row["adoption_status"] = "adopted"
                row["adoption_error"] = None

        with mock.patch.object(
            project_journal.time,
            "monotonic",
            side_effect=lambda: clock["now"],
        ):
            with mock.patch.object(
                project_journal,
                "_repo_root_for_path",
                side_effect=resolve_candidate,
            ):
                with mock.patch.object(
                    project_journal,
                    "_enrich_discovered_repo",
                    side_effect=enrich_candidate,
                ):
                    rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(enrichment_budgets, [0.5, 5.0])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["adoption_status"], "adopted")
        self.assertEqual(rows[0]["rollout_count"], 1)

    def test_discover_repos_bounds_persistent_inconclusive_retries(self) -> None:
        repo = self.init_repo("healthy").resolve()
        candidates = [self.root / f"candidate-{index}" for index in range(4)]
        remaining_budgets = [0.5, 2.0, 4.0, 8.0]
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-persistent-inconclusive.jsonl").write_text(
            "".join(
                json.dumps({"payload": {"cwd": str(candidate)}}) + "\n"
                for candidate in candidates
            ),
            encoding="utf-8",
        )
        clock = {"now": 100.0}
        remaining_by_candidate = dict(
            zip((str(candidate) for candidate in candidates), remaining_budgets)
        )
        resolved: list[str] = []
        enrichment_budgets: list[float] = []

        def resolve_candidate(
            path_text: str,
            *,
            codex_home: pathlib.Path | None = None,
            deadline: float | None = None,
        ) -> pathlib.Path:
            del codex_home
            self.assertIsNotNone(deadline)
            assert deadline is not None
            resolved.append(path_text)
            clock["now"] = deadline - remaining_by_candidate[path_text]
            return repo

        def enrich_candidate(
            root: pathlib.Path,
            row: dict[str, object],
            script: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> None:
            del script
            self.assertEqual(root, repo)
            self.assertIsNotNone(deadline)
            assert deadline is not None
            enrichment_budgets.append(deadline - clock["now"])
            row["adoption_status"] = "inconclusive"
            row["adoption_error"] = {"code": "persistent-test-failure"}

        with mock.patch.object(
            project_journal.time,
            "monotonic",
            side_effect=lambda: clock["now"],
        ):
            with mock.patch.object(
                project_journal,
                "_repo_root_for_path",
                side_effect=resolve_candidate,
            ):
                with mock.patch.object(
                    project_journal,
                    "_enrich_discovered_repo",
                    side_effect=enrich_candidate,
                ):
                    rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(resolved, [str(candidate) for candidate in candidates])
        self.assertEqual(
            len(enrichment_budgets),
            project_journal.DISCOVERY_ENRICHMENT_MAX_ATTEMPTS_PER_ROOT,
        )
        self.assertEqual(enrichment_budgets, [0.5, 2.0])
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["adoption_status"], "inconclusive")
        self.assertEqual(rows[0]["rollout_count"], 1)

    def test_discover_repos_does_not_retry_auxiliary_only_inconclusive(
        self,
    ) -> None:
        repo = self.init_repo("healthy").resolve()
        candidates = [self.root / "first", self.root / "second"]
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-auxiliary-only.jsonl").write_text(
            "".join(
                json.dumps({"payload": {"cwd": str(candidate)}}) + "\n"
                for candidate in candidates
            ),
            encoding="utf-8",
        )
        clock = {"now": 100.0}
        enrichment_calls = 0

        def resolve_candidate(
            path_text: str,
            *,
            codex_home: pathlib.Path | None = None,
            deadline: float | None = None,
        ) -> pathlib.Path:
            del path_text, codex_home
            self.assertIsNotNone(deadline)
            assert deadline is not None
            clock["now"] = deadline - (0.5 if enrichment_calls == 0 else 8.0)
            return repo

        def enrich_candidate(
            root: pathlib.Path,
            row: dict[str, object],
            script: pathlib.Path,
            *,
            deadline: float | None = None,
        ) -> None:
            nonlocal enrichment_calls
            del root, script, deadline
            enrichment_calls += 1
            row["adoption_status"] = "adopted"
            row["adoption_error"] = None
            row["discovery_status"] = "inconclusive"
            row["discovery_error"] = {
                "hooks_installed": {"code": "repo_discovery_failed"}
            }

        with mock.patch.object(
            project_journal.time,
            "monotonic",
            side_effect=lambda: clock["now"],
        ):
            with mock.patch.object(
                project_journal,
                "_repo_root_for_path",
                side_effect=resolve_candidate,
            ):
                with mock.patch.object(
                    project_journal,
                    "_enrich_discovered_repo",
                    side_effect=enrich_candidate,
                ):
                    rows = project_journal._discover_repos(codex_home, 9999)

        self.assertEqual(enrichment_calls, 1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["adoption_status"], "adopted")
        self.assertEqual(rows[0]["discovery_status"], "inconclusive")

    def test_discover_repos_isolates_worktree_journal_count_limit(self) -> None:
        healthy = self.init_repo("healthy")
        healthy_journal = self.write_journal(
            healthy,
            "docs/project_journal/2026/05/2026-05-05-healthy-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Healthy",
            status="active",
            updated="2026-05-05",
        )
        add = run_git(
            healthy,
            "add",
            "--",
            str(healthy_journal.relative_to(healthy)),
        )
        self.assertEqual(add.returncode, 0, add.stderr)

        oversized = self.init_repo("oversized")
        oversized_journal = self.write_journal(
            oversized,
            "docs/project_journal/2026/05/2026-05-05-oversized-d4e5f6.md",
            entry_id="20260505-d4e5f6",
            title="Oversized",
            status="active",
            updated="2026-05-05",
        )
        oversized_add = run_git(
            oversized,
            "add",
            "--",
            str(oversized_journal.relative_to(oversized)),
        )
        self.assertEqual(oversized_add.returncode, 0, oversized_add.stderr)
        journal_dir = oversized / "docs/project_journal/2026/05"
        for index in range(project_journal.MAX_JOURNAL_ENTRIES):
            (journal_dir / f"entry-{index:04d}.md").touch()

        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-count-limit.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(healthy)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(oversized)}})
            + "\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = {
            pathlib.Path(row["repo"]).name: row for row in json.loads(result.stdout)
        }
        self.assertEqual(set(rows), {"healthy", "oversized"})
        self.assertEqual(rows["healthy"]["discovery_status"], "complete")
        self.assertEqual(rows["healthy"]["journal_count"], 1)
        self.assertEqual(rows["healthy"]["adoption_status"], "adopted")
        self.assertEqual(rows["oversized"]["discovery_status"], "inconclusive")
        self.assertEqual(
            rows["oversized"]["discovery_error"]["journal_count"]["code"],
            "journal_semantic_limit_exceeded",
        )
        self.assertEqual(rows["oversized"]["adoption_status"], "adopted")
        self.assertIsNone(rows["oversized"]["adoption_error"])
        self.assertTrue(rows["oversized"]["tracked_journal_adopted"])
        self.assertEqual(rows["oversized"]["valid_tracked_journal_count"], 1)
        self.assertIsNone(rows["oversized"]["journal_count"])
        self.assertFalse(rows["oversized"]["index_ignored"])
        self.assertFalse(rows["oversized"]["hooks_installed"])

    def test_discover_repos_resolves_deleted_rollout_cwd_from_existing_parent(
        self,
    ) -> None:
        repo = self.init_repo()
        deleted_cwd = repo / "deleted" / "nested"

        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-deleted-cwd.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(deleted_cwd)}}) + "\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(pathlib.Path(rows[0]["repo"]), repo.resolve())
        self.assertEqual(rows[0]["rollout_count"], 1)

    def test_discover_repos_defaults_to_codex_home_env(self) -> None:
        repo = self.init_repo()
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-env.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )

        env = {"CODEX_HOME": str(codex_home), "PATH": os.environ.get("PATH", "")}
        result = self.run_cli(
            "discover-repos",
            "--since-days",
            "9999",
            "--json",
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(pathlib.Path(rows[0]["repo"]), repo.resolve())

    def test_discover_repos_ignores_relative_cwd_values(self) -> None:
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-relative.jsonl").write_text(
            json.dumps({"payload": {"cwd": "."}}) + "\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(rows, [])

    def test_discover_repos_maps_codex_worktree_to_source_repo(self) -> None:
        repo = self.init_repo()
        commit = run_git(
            repo,
            "-c",
            "commit.gpgSign=false",
            "-c",
            "user.name=Project Journal Test",
            "-c",
            "user.email=project-journal@example.test",
            "commit",
            "--allow-empty",
            "-m",
            "Initial commit",
        )
        self.assertEqual(commit.returncode, 0, commit.stderr)

        codex_home = self.root / "codex-home"
        codex_worktree = codex_home / "worktrees/c122/repo"
        codex_worktree.parent.mkdir(parents=True)
        add_worktree = run_git(
            repo,
            "worktree",
            "add",
            "-b",
            "codex-test-worktree",
            str(codex_worktree),
            "HEAD",
        )
        self.assertEqual(add_worktree.returncode, 0, add_worktree.stderr)

        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-worktree.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(codex_worktree)}}) + "\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(pathlib.Path(rows[0]["repo"]), repo.resolve())

    def test_discover_repos_keeps_normal_repo_under_codex_worktrees(self) -> None:
        codex_home = self.root / "codex-home"
        repo_parent = codex_home / "worktrees/c122"
        repo_parent.mkdir(parents=True)
        repo = self.init_repo("codex-home/worktrees/c122/repo")

        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-normal-repo.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}}) + "\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(pathlib.Path(rows[0]["repo"]), repo.resolve())

    def test_discover_repos_deduplicates_cwd_resolution(self) -> None:
        repo = self.init_repo()
        nested = repo / "nested"
        nested.mkdir()
        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-one.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(repo)}})
            + "\n",
            encoding="utf-8",
        )
        (rollout_dir / "rollout-two.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(repo)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(nested)}})
            + "\n"
            + json.dumps({"payload": {"cwd": str(nested)}})
            + "\n",
            encoding="utf-8",
        )

        calls: list[str] = []
        original = project_journal._repo_root_for_path

        def fake_repo_root_for_path(
            path_text: str,
            *,
            codex_home: pathlib.Path | None = None,
            deadline: float | None = None,
        ) -> pathlib.Path | None:
            self.assertIsNotNone(codex_home)
            self.assertIsNotNone(deadline)
            calls.append(path_text)
            return repo.resolve()

        try:
            project_journal._repo_root_for_path = fake_repo_root_for_path
            rows = project_journal._discover_repos(codex_home, 9999)
        finally:
            project_journal._repo_root_for_path = original

        self.assertEqual(calls, [str(repo), str(nested)])
        self.assertEqual(len(rows), 1)
        self.assertEqual(pathlib.Path(rows[0]["repo"]), repo.resolve())
        self.assertEqual(rows[0]["rollout_count"], 2)

    def test_discover_repos_maps_isolated_review_workspace_to_source_repo(self) -> None:
        repo = self.init_repo()
        self.write_journal(
            repo,
            "docs/project_journal/2026/05/2026-05-05-alpha-a1b2c3.md",
            entry_id="20260505-a1b2c3",
            title="Alpha Work",
            status="active",
            updated="2026-05-05",
        )
        isolated_workspace = repo / ".codex-tmp/isolated-review-a1b2c3/workspace"

        codex_home = self.root / "codex-home"
        rollout_dir = codex_home / "sessions/2026/05/05"
        rollout_dir.mkdir(parents=True)
        (rollout_dir / "rollout-isolated.jsonl").write_text(
            json.dumps({"payload": {"cwd": str(isolated_workspace)}}) + "\n",
            encoding="utf-8",
        )

        result = self.run_cli(
            "discover-repos",
            "--codex-home",
            str(codex_home),
            "--since-days",
            "9999",
            "--json",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        rows = json.loads(result.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(pathlib.Path(rows[0]["repo"]), repo.resolve())


if __name__ == "__main__":
    unittest.main()
