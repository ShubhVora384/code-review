"""The sandbox executes untrusted code, so its limits are the property worth
testing — not its happy path."""

import pytest

from app.proof.sandbox import (
    SandboxUnavailableError,
    SubprocessSandbox,
    get_sandbox,
    reset_sandbox_cache,
)


@pytest.fixture(autouse=True)
def _dev_env(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "app_env", "development")
    monkeypatch.setattr(config.settings, "proof_timeout_sec", 10)
    reset_sandbox_cache()
    yield
    reset_sandbox_cache()


@pytest.fixture
def box():
    return SubprocessSandbox()


async def test_runs_a_script_and_captures_output(box):
    result = await box.run("print('hello from the sandbox')")
    assert result.ok
    assert "hello from the sandbox" in result.stdout


async def test_supporting_files_are_importable(box):
    result = await box.run(
        "import target_module; print(target_module.answer())",
        files={"target_module.py": "def answer():\n    return 42\n"},
    )
    assert result.ok
    assert "42" in result.stdout


async def test_nonzero_exit_is_reported(box):
    result = await box.run("raise SystemExit(3)")
    assert not result.ok
    assert result.exit_code == 3


async def test_a_hanging_script_is_killed(box):
    result = await box.run("import time\nwhile True:\n    time.sleep(1)\n", timeout=3)
    assert result.timed_out
    assert not result.ok


async def test_runaway_memory_is_capped(box, monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "proof_memory_mb", 64)
    result = await box.run("x = bytearray(512 * 1024 * 1024)\nprint('allocated')")
    assert "allocated" not in result.stdout


async def test_files_are_read_back_for_tamper_checks(box):
    result = await box.run(
        "open('target_module.py', 'w').write('# rewritten\\n')",
        files={"target_module.py": "ORIGINAL = 1\n"},
        read_back=["target_module.py"],
    )
    assert result.files_after["target_module.py"] == "# rewritten\n"


async def test_untouched_file_reads_back_identical(box):
    source = "def f():\n    return 'ok'\n"
    result = await box.run(
        "import target_module; print(target_module.f())",
        files={"target_module.py": source},
        read_back=["target_module.py"],
    )
    assert result.files_after["target_module.py"] == source


async def test_writing_outside_the_sandbox_is_refused(box):
    with pytest.raises(ValueError, match="outside the sandbox"):
        await box.run("print(1)", files={"../escape.py": "x = 1"})


async def test_subprocess_backend_refuses_outside_development(box, monkeypatch):
    """The whole point: it cannot contain hostile code, so it must not run
    anywhere real."""
    from app.core import config

    monkeypatch.setattr(config.settings, "app_env", "production")
    with pytest.raises(SandboxUnavailableError, match="development-only"):
        await box.run("print('should never run')")


async def test_selection_returns_none_when_disabled(monkeypatch):
    from app.core import config

    monkeypatch.setattr(config.settings, "proof_sandbox", "disabled")
    reset_sandbox_cache()
    assert await get_sandbox() is None


async def test_selection_refuses_docker_when_unavailable(monkeypatch):
    from app.core import config
    from app.proof import sandbox as sandbox_mod

    monkeypatch.setattr(config.settings, "proof_sandbox", "docker")

    async def _no_docker(self):
        return False

    monkeypatch.setattr(sandbox_mod.DockerSandbox, "available", _no_docker)
    reset_sandbox_cache()
    with pytest.raises(SandboxUnavailableError, match="not reachable"):
        await get_sandbox()


async def test_auto_falls_back_to_subprocess_only_in_development(monkeypatch):
    from app.core import config
    from app.proof import sandbox as sandbox_mod

    async def _no_docker(self):
        return False

    monkeypatch.setattr(sandbox_mod.DockerSandbox, "available", _no_docker)
    monkeypatch.setattr(config.settings, "proof_sandbox", "auto")

    monkeypatch.setattr(config.settings, "app_env", "development")
    reset_sandbox_cache()
    chosen = await get_sandbox()
    assert chosen is not None and chosen.name == "subprocess"

    monkeypatch.setattr(config.settings, "app_env", "production")
    reset_sandbox_cache()
    assert await get_sandbox() is None, "production must not silently degrade"
