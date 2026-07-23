from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
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
        self.empty_gitconfig = self.root / "empty-gitconfig"
        self.empty_gitconfig.write_text("", encoding="utf-8")

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
            "GIT_CONFIG_GLOBAL": str(self.empty_gitconfig),
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
        completed_bytes = subprocess.CompletedProcess([], 0, b"", b"")

        with mock.patch.dict(os.environ, poison, clear=False):
            with mock.patch.object(
                project_journal.subprocess,
                "run",
                side_effect=(completed_text, completed_bytes),
            ) as run:
                project_journal._run_git(repo, "rev-parse", "--show-toplevel")
                project_journal._run_git_bytes(repo, "cat-file", "-s", "a" * 40)

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
        self.assertIn("failed to inspect index blob", result.stderr)
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

        original_read = project_journal._read_index_blob
        mutated = False

        def read_then_mutate(
            repo_arg: pathlib.Path,
            blob: project_journal.IndexJournalBlob,
        ) -> bytes:
            nonlocal mutated
            content = original_read(repo_arg, blob)
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
            return content

        with mock.patch.object(
            project_journal,
            "_read_index_blob",
            side_effect=read_then_mutate,
        ):
            with self.assertRaisesRegex(
                project_journal.UserError,
                "Git index changed during validation",
            ):
                project_journal._load_entries_from_index(repo)

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
            if process.poll() is not None:
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
        self.assertIn("monotonic time, byte, record, and stderr limits", skill)
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
        self.assertIn("byte, record, and stderr bounds", readme)
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

    def test_install_hooks_ignores_ambient_global_hooks_path(self) -> None:
        repo = self.init_repo()
        global_config = self.root / "global-gitconfig"
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
        env = {
            "GIT_CONFIG_GLOBAL": str(global_config),
            "PATH": os.environ.get("PATH", ""),
        }

        result = self.run_cli("install-hooks", "--repo", str(repo), env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue((repo / ".git/hooks/post-merge").exists())
        self.assertFalse((global_hooks / "post-merge").exists())

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
        self.assertFalse(rows[0]["tracked_journal_adopted"])
        self.assertEqual(rows[0]["valid_tracked_journal_count"], 0)
        self.assertEqual(rows[0]["rollout_count"], 1)
        self.assertFalse(rows[0]["hooks_installed"])

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
