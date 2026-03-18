"""Tests for Redis YAML configuration."""
import os
import pytest

from onestep.config import load_app_config

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")


class TestRedisYamlConfig:
    """Tests for Redis connector YAML configuration."""

    def test_redis_connector_config(self):
        """Should parse Redis connector from YAML config."""
        config = {
            "name": "test-app",
            "connectors": {
                "redis": {
                    "type": "redis",
                    "url": REDIS_URL,
                }
            },
            "tasks": []
        }
        app = load_app_config(config)
        assert app.name == "test-app"

    def test_redis_stream_config(self):
        """Should parse redis_stream resource from YAML config."""
        config = {
            "name": "test-app",
            "connectors": {
                "redis": {
                    "type": "redis",
                    "url": REDIS_URL,
                },
                "jobs": {
                    "type": "redis_stream",
                    "connector": "redis",
                    "stream": "test:jobs",
                    "group": "test-workers",
                    "batch_size": 50,
                }
            },
            "tasks": []
        }
        app = load_app_config(config)
        assert app.name == "test-app"

    def test_redis_stream_with_maxlen(self):
        """Should parse maxlen option for redis_stream."""
        config = {
            "name": "test-app",
            "connectors": {
                "redis": {
                    "type": "redis",
                    "url": REDIS_URL,
                },
                "bounded_stream": {
                    "type": "redis_stream",
                    "connector": "redis",
                    "stream": "test:bounded",
                    "group": "test",
                    "maxlen": 1000,
                    "approximate_trim": False,
                }
            },
            "tasks": []
        }
        app = load_app_config(config)
        assert app.name == "test-app"

    def test_redis_stream_defaults(self):
        """Should use sensible defaults for redis_stream."""
        config = {
            "name": "test-app",
            "connectors": {
                "redis": {
                    "type": "redis",
                    "url": REDIS_URL,
                },
                "default_stream": {
                    "type": "redis_stream",
                    "connector": "redis",
                    "stream": "test:default",
                }
            },
            "tasks": []
        }
        app = load_app_config(config)
        assert app.name == "test-app"

    def test_full_redis_app_config(self, tmp_path, monkeypatch):
        """Should load a full Redis app from YAML config."""
        # Add example to sys.path for handler import
        import sys
        example_path = str(tmp_path.parent.parent / "example")
        if example_path not in sys.path:
            sys.path.insert(0, example_path)
        
        yaml_content = f"""
app:
  name: redis-test-app
  shutdown_timeout_s: 5.0

connectors:
  redis:
    type: redis
    url: "{REDIS_URL}"
  
  jobs:
    type: redis_stream
    connector: redis
    stream: test:jobs
    group: test-workers
    batch_size: 10
  
  output:
    type: redis_stream
    connector: redis
    stream: test:output
    group: test-output

tasks: []
"""
        yaml_file = tmp_path / "app.yaml"
        yaml_file.write_text(yaml_content)

        from onestep.config import load_yaml_app
        app = load_yaml_app(str(yaml_file))
        
        assert app.name == "redis-test-app"
        assert app.shutdown_timeout_s == 5.0


# Handler function for YAML tests
async def sample_handler(ctx, item):
    """Sample handler for testing YAML config."""
    return {"processed": item}