import os
import sys
from pathlib import Path

# Tests must never touch a real datastore or the Groq API.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./test.db")
os.environ.setdefault("REDIS_URL", "")
os.environ.setdefault("GROQ_API_KEY", "")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest


@pytest.fixture
def sample_project(tmp_path: Path) -> Path:
    """A tiny repo with a gitignore, a binary, and a vendored directory."""
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text(
        "import os\n\n\ndef main():\n    print('hi')\n" * 4
    )
    (tmp_path / "src" / "util.py").write_text("def add(a, b):\n    return a + b\n")
    (tmp_path / "README.md").write_text("# Sample\nA sample project.\n")
    (tmp_path / "package.json").write_text('{"name": "sample"}\n')
    (tmp_path / ".gitignore").write_text("ignored/\n*.log\n")

    (tmp_path / "ignored").mkdir()
    (tmp_path / "ignored" / "secret.py").write_text("TOKEN = 1\n")
    (tmp_path / "debug.log").write_text("noise\n")

    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "dep.js").write_text("module.exports = 1\n")

    (tmp_path / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    return tmp_path
