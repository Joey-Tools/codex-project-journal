from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shlex
import shutil
import signal
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
        self, *args: str, env: dict[str, str] | None = None
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
        completed_text = subprocess.CompletedProcess([], 0, "", "")

        with mock.patch.dict(os.environ, poison, clear=False):
            with mock.patch.object(
                project_journal.subprocess,
                "run",
                return_value=completed_text,
            ) as run:
                project_journal._run_git(repo, "rev-parse", "--show-toplevel")

        expected_git_env = {
            key: value
            for key, value in project_journal.SAFE_GIT_ENV.items()
            if key.startswith("GIT_")
        }
        for call in run.call_args_list:
            child_env = call.kwargs["env"]
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
            self.assertIn("--no-optional-locks", call.args[0])
            self.assertIn("core.fsmonitor=false", call.args[0])
            self.assertIn(
                f"core.hooksPath={os.devnull}",
                call.args[0],
            )
            self.assertIn(
                f"core.attributesFile={os.devnull}",
                call.args[0],
            )

    def test_git_runtime_is_fixed_absolute_and_meets_minimum_version(self) -> None:
        runtime = project_journal._GIT_RUNTIME

        self.assertIsNotNone(runtime)
        assert runtime is not None
        self.assertTrue(runtime.executable.is_absolute())
        self.assertEqual(runtime.executable, runtime.executable.resolve())
        self.assertGreaterEqual(runtime.version, project_journal.MINIMUM_GIT_VERSION)
        command = project_journal._git_command(
            self.root,
            "rev-parse",
            "--show-toplevel",
        )
        self.assertEqual(command[0], str(runtime.executable))

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
        self.assertEqual(pathlib.Path(rows[0]["repo"]), repo.resolve())
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
        for term_target_existed, group_exists, expected_signals in (
            (False, False, [signal.SIGTERM]),
            (True, False, [signal.SIGTERM]),
            (True, True, [signal.SIGTERM, kill_signal]),
        ):
            with self.subTest(
                term_target_existed=term_target_existed,
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

                def signal_group(
                    process_arg: subprocess.Popen[bytes],
                    sig: int,
                ) -> project_journal._ProcessSignalResult:
                    self.assertIs(process_arg, process)
                    events.append(("signal", sig))
                    return project_journal._ProcessSignalResult(
                        target_existed=(
                            term_target_existed if sig == signal.SIGTERM else True
                        )
                    )

                def probe_group(
                    process_arg: subprocess.Popen[bytes],
                    deadline: float,
                ) -> tuple[bool, None]:
                    self.assertIs(process_arg, process)
                    self.assertGreater(deadline, 0)
                    events.append(("probe", None))
                    return group_exists, None

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
                expected_events = [
                    ("signal", signal.SIGTERM),
                ]
                if term_target_existed:
                    expected_events.append(("probe", None))
                if group_exists:
                    expected_events.append(("signal", kill_signal))
                expected_events.extend(
                    [
                        ("status", None),
                        ("wait", None),
                    ]
                )
                self.assertEqual(events, expected_events)
                process.poll.assert_not_called()

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
                "_terminate_process_group_and_reap",
            ) as cleanup:
                with self.assertRaises(KeyboardInterrupt):
                    self.capture_process(
                        [sys.executable, "-c", "pass"],
                        timeout_seconds=5,
                        stdout_limit=1024,
                    )

        cleanup.assert_not_called()

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
                "_terminate_process_group_and_reap",
            ) as cleanup:
                with self.assertRaises(KeyboardInterrupt) as raised:
                    self.capture_process(
                        [sys.executable, "-c", "pass"],
                        timeout_seconds=5,
                        stdout_limit=1024,
                    )

        cleanup.assert_not_called()
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
        ) -> str:
            actual_error = original_cleanup(process, selector)
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
                "cleanup-incomplete: .*simulated unreaped process group",
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
            "index, entry, frontmatter field/list, validation-issue, byte, record, and stderr limits",
            skill,
        )
        self.assertIn("one bounded `git cat-file --batch` session", skill)
        self.assertIn("final raw index revalidation", skill)
        self.assertIn("stays unreaped as the PID/PGID identity fence", skill)
        self.assertIn("status is observed with `WNOWAIT`", skill)
        self.assertIn("explicit ownership states", skill)
        self.assertIn(
            "no numeric PGID is signalled after that fence is released",
            skill,
        )
        self.assertIn("reported as `cleanup-incomplete`", skill)
        self.assertIn(
            "`inconclusive` carries a structured `adoption_error` and null adoption fields",
            skill,
        )
        self.assertIn("actual global Git config", skill)
        self.assertIn("without following includes", skill)
        self.assertIn("explicit repo-local `core.hooksPath`", skill)
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
        self.assertIn("requires a bounded credential-free version result", readme)
        self.assertIn("byte, record, and stderr bounds", readme)
        self.assertIn("one bounded `git cat-file --batch` session", readme)
        self.assertIn("one absolute monotonic deadline", readme)
        self.assertIn("structured per-path validity", readme)
        self.assertIn("entry/field/list/issue budgets", readme)
        self.assertIn("unreaped direct child fences the PID/PGID", readme)
        self.assertIn("Explicit cleanup ownership states", readme)
        self.assertIn("never signals a numeric PGID after the final reap", readme)
        self.assertIn("reports `cleanup-incomplete`", readme)
        self.assertIn("global `core.hooksPath`", readme)
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

    def test_install_hooks_refuses_symlinked_default_hooks_dir(self) -> None:
        repo = self.init_repo()
        hooks_dir = repo / ".git/hooks"
        shared_hooks = self.root / "shared-hooks"
        shared_hooks.mkdir()
        shutil.rmtree(hooks_dir)
        hooks_dir.symlink_to(shared_hooks, target_is_directory=True)

        result = self.run_cli("install-hooks", "--repo", str(repo))

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hook directory links", result.stderr)
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
            path_text: str, *, codex_home: pathlib.Path | None = None
        ) -> pathlib.Path | None:
            self.assertIsNotNone(codex_home)
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
