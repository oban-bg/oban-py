from textwrap import dedent

import psycopg
import pytest
from click.testing import CliRunner

from oban.cli import _import_cron_paths, main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def dsn(test_dsn, dsn_base):
    name = "oban_cli_test"

    with psycopg.connect(f"{dsn_base}/postgres", autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')
        conn.execute(f'CREATE DATABASE "{name}"')

    yield f"{dsn_base}/{name}"

    with psycopg.connect(f"{dsn_base}/postgres", autocommit=True) as conn:
        conn.execute(f'DROP DATABASE IF EXISTS "{name}"')


class TestImportCronPaths:
    def test_discovers_multi_line_worker(self, tmp_path, monkeypatch):
        (tmp_path / "cron_worker.py").write_text(
            dedent("""
            from oban import worker

            @worker(
                cron="*/5 * * * *",
                queue="default",
            )
            class FrequentWorker:
                async def process(self, job):
                    pass
        """)
        )

        monkeypatch.chdir(tmp_path)

        assert _import_cron_paths([str(tmp_path)]) == ["cron_worker"]

    def test_discovers_multi_line_job(self, tmp_path, monkeypatch):
        (tmp_path / "cron_job.py").write_text(
            dedent("""
            from oban import job

            @job(
                cron="*/5 * * * *",
                queue="default",
            )
            def execute():
                pass
        """)
        )

        monkeypatch.chdir(tmp_path)

        assert _import_cron_paths([str(tmp_path)]) == ["cron_job"]

    def test_ignores_worker_without_cron(self, tmp_path, monkeypatch):
        (tmp_path / "no_cron.py").write_text(
            dedent("""
            from oban import worker

            @worker(queue="default")
            class RegularWorker:
                async def process(self, job):
                    pass
        """)
        )

        monkeypatch.chdir(tmp_path)

        assert _import_cron_paths([str(tmp_path)]) == []


class TestInstallCommand:
    def test_install_creates_schema(self, runner, dsn):
        result = runner.invoke(main, ["install", "--dsn", dsn])
        assert result.exit_code == 0

    def test_install_with_env_var(self, runner, dsn):
        result = runner.invoke(main, ["install"], env={"OBAN_DSN": dsn})
        assert result.exit_code == 0

    def test_install_without_dsn_fails(self, runner):
        assert runner.invoke(main, ["install"]).exit_code > 0


class TestUninstallCommand:
    def test_uninstall_removes_schema(self, runner, dsn):
        runner.invoke(main, ["install", "--dsn", dsn])
        result = runner.invoke(main, ["uninstall", "--dsn", dsn])
        assert result.exit_code == 0

    def test_uninstall_with_env_var(self, runner, dsn):
        runner.invoke(main, ["install"], env={"OBAN_DSN": dsn})
        result = runner.invoke(main, ["uninstall"], env={"OBAN_DSN": dsn})
        assert result.exit_code == 0


class TestStartCommand:
    def test_start_with_env_vars(self, runner, dsn):
        runner.invoke(main, ["install", "--dsn", dsn])

        env = {
            "OBAN_DSN": dsn,
            "OBAN_QUEUES": "default:10,mailers:5",
        }

        assert runner.invoke(main, ["start", "--help"], env=env).exit_code == 0

    def test_start_with_params(self, runner, dsn):
        runner.invoke(main, ["install", "--dsn", dsn])

        result = runner.invoke(
            main,
            ["start", "--dsn", dsn, "--queues", "default:10", "--dry-run"],
        )

        assert result.exit_code == 0

    def test_start_with_cron_modules(self, runner, dsn):
        runner.invoke(main, ["install", "--dsn", dsn])

        result = runner.invoke(
            main,
            ["start", "--dsn", dsn, "--cron-modules", "os,sys", "--dry-run"],
        )

        assert result.exit_code == 0

    def test_start_with_cron_paths(self, runner, dsn, tmp_path, monkeypatch):
        runner.invoke(main, ["install", "--dsn", dsn])

        worker_file = tmp_path / "test_worker.py"
        worker_file.write_text(
            dedent("""
            from oban import worker

            @worker(cron="@daily")
            class DailyWorker:
                async def process(self, job):
                    pass
        """)
        )

        monkeypatch.chdir(tmp_path)

        result = runner.invoke(
            main,
            ["start", "--dsn", dsn, "--cron-paths", "*.py", "--dry-run"],
        )

        assert result.exit_code == 0
