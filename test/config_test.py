from textwrap import dedent

from oban.config import Config


class TestFromEnv:
    def test_from_env_with_all_params(self, monkeypatch):
        monkeypatch.setenv("OBAN_DATABASE_URL", "postgresql://localhost/test")
        monkeypatch.setenv("OBAN_QUEUES", "default:10,mailers:5")
        monkeypatch.setenv("OBAN_PREFIX", "custom")
        monkeypatch.setenv("OBAN_NODE", "node1")
        monkeypatch.setenv("OBAN_POOL_MIN_SIZE", "2")
        monkeypatch.setenv("OBAN_POOL_MAX_SIZE", "20")

        conf = Config.from_env()

        assert conf.database_url == "postgresql://localhost/test"
        assert conf.queues == {"default": 10, "mailers": 5}
        assert conf.prefix == "custom"
        assert conf.node == "node1"
        assert conf.pool_min_size == 2
        assert conf.pool_max_size == 20

    def test_from_env_with_empty_queues(self, monkeypatch):
        monkeypatch.setenv("OBAN_QUEUES", "")

        conf = Config.from_env()

        assert conf.queues == {}

    def test_from_env_skips_malformed_queue_specs(self, monkeypatch):
        monkeypatch.setenv("OBAN_DATABASE_URL", "postgresql://localhost/test")
        monkeypatch.setenv("OBAN_QUEUES", "default:10,invalid,mailers:5")

        conf = Config.from_env()

        assert conf.queues == {"default": 10, "mailers": 5}


class TestFromCli:
    def test_from_cli_with_all_params(self):
        params = {
            "database_url": "postgresql://localhost/test",
            "queues": "default:10, mailers:5",
            "prefix": "custom",
            "node": "node1",
            "pool_min_size": 2,
            "pool_max_size": 20,
        }

        conf = Config.from_cli(params)

        assert conf.database_url == "postgresql://localhost/test"
        assert conf.queues == {"default": 10, "mailers": 5}
        assert conf.prefix == "custom"
        assert conf.node == "node1"
        assert conf.pool_min_size == 2
        assert conf.pool_max_size == 20


class TestMerge:
    def test_merge_cli_overrides_env(self):
        conf_a = Config(
            database_url="postgresql://localhost/env_db",
            queues={"default": 5},
            prefix="env_prefix",
            pool_min_size=1,
        )

        conf_b = Config(
            database_url="postgresql://localhost/cli_db",
            queues={"default": 10, "mailers": 3},
            prefix="cli_prefix",
        )

        conf = conf_a.merge(conf_b)

        assert conf.database_url == "postgresql://localhost/cli_db"
        assert conf.queues == {"default": 10, "mailers": 3}
        assert conf.prefix == "cli_prefix"
        assert conf.pool_min_size == 1

    def test_merge_combines_queues(self):
        conf_a = Config(
            database_url="postgresql://localhost/test",
            queues={"default": 5, "emails": 2},
        )

        conf_b = Config(queues={"default": 10, "mailers": 3})

        conf = conf_a.merge(conf_b)

        assert conf.queues == {"default": 10, "emails": 2, "mailers": 3}

    def test_merge_dict_configs(self):
        conf_a = Config(
            database_url="postgresql://localhost/test",
            pruner={"max_age": 3600, "interval": 60},
        )

        conf_b = Config(pruner={"interval": 120})

        conf = conf_a.merge(conf_b)

        assert conf.pruner == {"max_age": 3600, "interval": 120}


class TestFromToml:
    def test_from_toml_with_explicit_path(self, tmp_path):
        config_file = tmp_path / "test_config.toml"
        config_file.write_text(
            dedent(
                """
                database_url = "postgresql://localhost/test"
                pool_min_size = 2

                [queues]
                default = 10
                mailers = 5

                [scheduler]
                timezone = "America/New_York"
                """
            ).strip()
        )

        conf = Config.from_toml(str(config_file))

        assert conf.database_url == "postgresql://localhost/test"
        assert conf.queues == {"default": 10, "mailers": 5}
        assert conf.pool_min_size == 2
        assert conf.scheduler == {"timezone": "America/New_York"}

    def test_from_toml_searches_oban_toml(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        config_file = tmp_path / "oban.toml"
        config_file.write_text(
            dedent(
                """
                database_url = "postgresql://localhost/test"

                [queues]
                default = 10
                """
            ).strip()
        )

        conf = Config.from_toml()

        assert conf.database_url == "postgresql://localhost/test"
        assert conf.queues == {"default": 10}

    def test_from_toml_returns_empty_config_when_no_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)

        conf = Config.from_toml()

        assert conf.database_url is None
        assert conf.queues == {}
