from __future__ import annotations

import errno
import importlib.util
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
from collections.abc import Callable
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


class ProjectJournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.home = self.root / "home"
        self.home.mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

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
    ) -> project_journal._GitRuntime:
        source = self.root / f"{name}-source"
        source.write_text(
            textwrap.dedent(
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
            version=(2, 45, 1),
            digest=digest,
            file_identity=snapshot_identity,
            directory_identity=directory_identity,
            snapshot_owner=snapshot_owner,
        )

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

    def test_adoption_status_rejects_empty_journal_directory(self) -> None:
        repo = self.init_repo()
        (repo / "docs/project_journal").mkdir(parents=True)

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
        self.assertEqual(runtime.executable.stat().st_mode & 0o777, 0o500)
        self.assertEqual(runtime.executable.parent.stat().st_mode & 0o777, 0o700)
        self.assertGreaterEqual(runtime.version, project_journal.MINIMUM_GIT_VERSION)
        command = project_journal._git_command(
            self.root,
            "rev-parse",
            "--show-toplevel",
        )
        self.assertEqual(command[0], str(runtime.executable))

    def test_git_gate_executes_bound_snapshot_after_source_replacement_and_rewrite(
        self,
    ) -> None:
        old_runtime = project_journal._GIT_RUNTIME
        old_error = project_journal._GIT_RUNTIME_ERROR
        fake_git = self.root / "git"
        safe_script = textwrap.dedent(
            """\
            #!/bin/sh
            if [ "$1" = "--version" ]; then
              printf 'git version 2.45.1\\n'
            else
              printf 'safe-snapshot\\n'
            fi
            """
        )
        malicious_script = textwrap.dedent(
            """\
            #!/bin/sh
            printf 'replaced-source\\n'
            """
        )

        try:
            for mutation in ("replacement", "same-inode-rewrite"):
                with self.subTest(mutation=mutation):
                    fake_git.write_text(safe_script, encoding="utf-8")
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
                        [str(project_journal._fixed_git_executable()), "probe"],
                        check=False,
                        text=True,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout, "safe-snapshot\n")
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
            self.assertEqual(pathlib.Path(argv[0]), runtime.executable)
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
            self.assertEqual(pathlib.Path(argv[0]), runtime.executable)
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
            self.assertEqual(pathlib.Path(argv[0]), runtime.executable)
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
                and candidate.name == "git"
                and candidate.parent.name.startswith("project-journal-git-launch-")
                and (flags & os.O_ACCMODE) == os.O_RDONLY
            ):
                replacement_attempted = True
                self.assertTrue(flags & project_journal.os.O_NONBLOCK)
                self.assertTrue(flags & project_journal.os.O_NOFOLLOW)
                self.assertEqual(candidate.parent.stat().st_mode & 0o777, 0o500)
                with self.assertRaises(PermissionError):
                    os.replace(attacker, candidate)
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
        actual_chmod = os.chmod
        attacker = self.root / "launch-prelock-attacker"
        attacker.write_text(
            "#!/bin/sh\nprintf 'attacker-executed\\n'\n",
            encoding="utf-8",
        )
        attacker.chmod(0o500)
        replaced = False

        def replace_launch_before_directory_lock(
            path: os.PathLike[str] | str,
            mode: int,
            *,
            dir_fd: int | None = None,
            follow_symlinks: bool = True,
        ) -> None:
            nonlocal replaced
            candidate = pathlib.Path(path)
            if (
                not replaced
                and mode == 0o500
                and candidate.name.startswith("project-journal-git-launch-")
            ):
                os.replace(attacker, candidate / "git")
                replaced = True
            actual_chmod(
                path,
                mode,
                dir_fd=dir_fd,
                follow_symlinks=follow_symlinks,
            )

        try:
            with mock.patch.object(
                project_journal.os,
                "chmod",
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
        ) -> str:
            actual_error = actual_cleanup(process, selector, ownership)
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

            self.assertIn("simulated unverified terminal state", str(raised.exception))
            self.assertIn("retained launch locator", str(raised.exception))
            self.assertIsNotNone(observed_launch)
            assert observed_launch is not None
            self.assertIn(str(observed_launch.parent), str(raised.exception))
            self.assertTrue(observed_launch.exists())
            self.assertEqual(observed_launch.parent.stat().st_mode & 0o777, 0o500)
        finally:
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

            self.assertIn(
                "simulated bound child identity loss",
                str(raised.exception),
            )
            self.assertIn("retained launch locator", str(raised.exception))
            self.assertIsNotNone(observed_launch)
            assert observed_launch is not None
            self.assertIn(str(observed_launch.parent), str(raised.exception))
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
                with self.assertRaises(project_journal.UserError) as raised:
                    project_journal._capture_bounded_process(
                        [str(runtime.executable), "probe"],
                        env={"PATH": os.environ.get("PATH", "")},
                        verified_runtime=runtime,
                        timeout_seconds=2,
                        stdout_limit=0,
                        stderr_limit=1024,
                        stdout_overflow_error="original stdout overflow",
                        stderr_overflow_error="stderr overflow",
                        timeout_error="launch timed out",
                        operation="cleanup-failing Git launch",
                    )

            self.assertIn("original stdout overflow", str(raised.exception))
            self.assertIn("launch-copy cleanup-incomplete", str(raised.exception))
            self.assertIn(
                "simulated launch cleanup failure",
                str(raised.exception),
            )
        finally:
            runtime.snapshot_owner.cleanup()

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

        def interrupt_process_start(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            nonlocal observed_launch
            observed_launch = pathlib.Path(str(kwargs["executable"]))
            raise KeyboardInterrupt("injected process-start interruption")

        try:
            with mock.patch.object(
                project_journal.subprocess,
                "Popen",
                side_effect=interrupt_process_start,
            ):
                with self.assertRaises(KeyboardInterrupt) as raised:
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
        fake_git = self.root / "version-gate-git"
        fake_git.write_text(
            "#!/bin/sh\nprintf 'git version 2.45.1\\n'\n",
            encoding="utf-8",
        )
        fake_git.chmod(0o755)
        attacker = self.root / "version-gate-attacker"
        attacker.write_text(
            "#!/bin/sh\nprintf 'git version 1.0.0\\n'\n",
            encoding="utf-8",
        )
        attacker.chmod(0o755)
        actual_popen = subprocess.Popen
        replaced = False

        def replace_snapshot_during_version_gate(
            argv: list[str],
            *args: object,
            **kwargs: object,
        ) -> subprocess.Popen[bytes]:
            nonlocal replaced
            snapshot = pathlib.Path(argv[0])
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
            self.assertEqual(runtime.version, (2, 45, 1))
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

    def test_old_git_fails_closed_and_discovery_reports_inconclusive(
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
        self.assertEqual(
            pathlib.Path(rows[0]["repo"]),
            project_journal._lexical_absolute_path(repo),
        )
        self.assertEqual(rows[0]["adoption_status"], "inconclusive")
        self.assertEqual(
            rows[0]["adoption_error"]["code"],
            "unsupported_git_version",
        )
        self.assertIn("Git >= 2.45 is required", rows[0]["adoption_error"]["message"])
        self.assertIsNone(rows[0]["index_ignored"])
        invocations = shim_log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(invocations, ["--version|lazy=1"])
        self.assertNotIn("fsmonitor", "\n".join(invocations))

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

        with self.assertRaisesRegex(
            project_journal.UserError,
            "entry count exceeds",
        ):
            project_journal._validate_entries(
                [per_entry] * (project_journal.MAX_JOURNAL_ENTRIES + 1)
            )

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

        malformed_outputs = (
            b"100644 " + oid + b" 0\t" + raw_path,
            b"100644 not-an-oid 0\t" + raw_path + b"\0",
            b"100644 " + oid + b" 0 docs/project_journal/bad.md\0",
            b"100644 " + oid + b" 0\tdocs/project_journal/../outside.md\0",
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

    def test_non_posix_runtime_is_explicitly_unsupported(self) -> None:
        original_runtime = project_journal._GIT_RUNTIME
        original_error = project_journal._GIT_RUNTIME_ERROR
        try:
            project_journal._GIT_RUNTIME = None
            project_journal._GIT_RUNTIME_ERROR = None
            with mock.patch.object(project_journal.os, "name", "nt"):
                project_journal._initialize_git_runtime()
            self.assertIsInstance(
                project_journal._GIT_RUNTIME_ERROR,
                project_journal.UnsupportedPlatform,
            )
            self.assertIn(
                "requires a POSIX host",
                str(project_journal._GIT_RUNTIME_ERROR),
            )
        finally:
            project_journal._GIT_RUNTIME = original_runtime
            project_journal._GIT_RUNTIME_ERROR = original_error

    @unittest.skipUnless(
        os.name == "posix" and hasattr(os, "WNOWAIT"),
        "POSIX WNOWAIT process-status contract",
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
                ) -> tuple[bool, None]:
                    self.assertIs(process_arg, process)
                    events.append(("initial-probe", None))
                    return initial_group_exists, None

                def probe_group(
                    process_arg: subprocess.Popen[bytes],
                    deadline: float,
                ) -> tuple[bool, None]:
                    nonlocal probe_calls
                    self.assertIs(process_arg, process)
                    self.assertGreater(deadline, 0)
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
                ) -> int:
                    self.assertIs(process_arg, process)
                    self.assertGreater(deadline, 0)
                    self.assertTrue(timeout_error)
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
                    expected_events.append(("post-kill-probe", None))
                expected_events.extend(
                    [
                        ("status", None),
                        ("wait", None),
                    ]
                )
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

    def test_bounded_capture_surfaces_cleanup_incomplete(self) -> None:
        parser = project_journal._IndexStageStreamParser()
        original_cleanup = project_journal._terminate_process_group_and_reap

        def cleanup_then_report(
            process: subprocess.Popen[bytes],
            selector: object,
            ownership: project_journal._ProcessOwnership,
        ) -> str:
            actual_error = original_cleanup(process, selector, ownership)
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
                "cleanup-incomplete(?: after exit 0)?: "
                ".*simulated unreaped process group",
            ):
                self.capture_process(
                    [sys.executable, "-c", "import os; os.write(1, b'incomplete')"],
                    timeout_seconds=5,
                    stdout_limit=1024,
                    stdout_feed=parser.feed,
                    stdout_finish=parser.finish,
                )

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
        self.assertIn("one absolute monotonic deadline", skill)
        self.assertIn("frontmatter parsing, semantic validation", skill)
        self.assertIn("frontmatter field/list, validation-issue", skill)
        self.assertIn("Per-path validation state is structured", skill)
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
        self.assertIn("stays unreaped as the PID/PGID identity fence", skill)
        self.assertIn("status is observed with `WNOWAIT`", skill)
        self.assertIn("Explicit ownership states", skill)
        self.assertIn(
            "requires a POSIX host and rejects other platforms",
            skill,
        )
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
            "repeats descriptor/path identity, access, size, and digest validation",
            skill,
        )
        self.assertIn(
            "an unverified terminal retains and reports the locator",
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
        self.assertIn("allowing timestamp-only transitions", skill)
        self.assertIn(
            "do not overwrite authoritative index adoption",
            skill,
        )
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
            "verified descriptor bytes into a fresh owner-private command launch",
            readme,
        )
        self.assertIn(
            "locks the launch directory against ordinary replacement",
            readme,
        )
        self.assertIn(
            "retains and reports its locator",
            readme,
        )
        self.assertIn("byte, record, and stderr bounds", readme)
        self.assertIn("one bounded `git cat-file --batch` session", readme)
        self.assertIn("one absolute monotonic deadline", readme)
        self.assertIn("structured per-path validity", readme)
        self.assertIn("entry/field/list/issue budgets", readme)
        self.assertIn("unreaped direct child fences the PID/PGID", readme)
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
        self.assertIn("bind the complete absolute path", readme)
        self.assertIn("structured `discovery_error`", readme)
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
        self.assertIn("preserved recovery locator", str(raised.exception))
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
                raise KeyboardInterrupt("injected after committed exchange")

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_then_interrupt,
        ):
            with self.assertRaises(KeyboardInterrupt) as raised:
                project_journal.command_install_hooks(args)

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
                raise KeyboardInterrupt("injected after displaced-hook unlink")

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal.os,
            "unlink",
            side_effect=unlink_then_interrupt,
        ):
            with self.assertRaises(KeyboardInterrupt) as raised:
                project_journal.command_install_hooks(args)

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
                raise KeyboardInterrupt("injected after committed no-replace rename")

        args = mock.Mock(repo=str(repo))
        with mock.patch.object(
            project_journal,
            "_rename_hook_entry_with_flag",
            side_effect=rename_then_interrupt,
        ):
            with self.assertRaises(KeyboardInterrupt) as raised:
                project_journal.command_install_hooks(args)

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
            project_journal._default_hook_path_plan(repo)
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
                project_journal._bind_hook_directory(plan)

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

    def test_path_date_skips_earlier_non_date_sessions_component(self) -> None:
        path = pathlib.Path(
            "/tmp/sessions/.codex/sessions/2026/05/05/rollout-test.jsonl"
        )

        dated = project_journal._path_date(path)

        self.assertIsNotNone(dated)
        self.assertEqual(dated.isoformat(), "2026-05-05")

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
        self.assertEqual(hook_error["code"], "repo_discovery_failed")
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
