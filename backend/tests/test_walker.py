from pathlib import Path

from app.ingestion.language import detect_language, is_probably_binary, is_test_file
from app.ingestion.walker import directory_summary, render_tree, walk_repository


def test_walker_respects_gitignore_and_skip_dirs(sample_project: Path):
    inv = walk_repository(sample_project)
    paths = {f.rel_path for f in inv.files}

    assert "src/app.py" in paths
    assert "README.md" in paths
    assert "ignored/secret.py" not in paths   # .gitignore
    assert "debug.log" not in paths           # .gitignore glob
    assert "node_modules/dep.js" not in paths # hardcoded skip dir
    assert "image.png" not in paths           # binary
    assert inv.skipped > 0


def test_inventory_metrics(sample_project: Path):
    inv = walk_repository(sample_project)
    assert inv.total_loc > 0
    assert inv.primary_language == "Python"
    assert "package.json" in {m.rel_path for m in inv.manifests}
    assert "README.md" in {m.rel_path for m in inv.manifests}
    assert all(f.reviewable for f in inv.reviewable_files)


def test_priority_orders_entrypoints_above_utilities(sample_project: Path):
    inv = walk_repository(sample_project)
    by_path = {f.rel_path: f for f in inv.files}
    assert by_path["src/app.py"].priority > by_path["src/util.py"].priority


def test_render_tree_and_summary(sample_project: Path):
    inv = walk_repository(sample_project)
    tree = render_tree(inv)
    assert "app.py" in tree and "loc" in tree

    dirs = directory_summary(inv)
    assert any(d["path"] == "src" for d in dirs)
    assert all(d["loc"] >= 0 for d in dirs)


def test_language_detection():
    assert detect_language(Path("a.py")) == "Python"
    assert detect_language(Path("a.tsx")) == "TypeScript"
    assert detect_language(Path("Dockerfile")) == "Dockerfile"
    assert detect_language(Path("weird.zzz")) == "Unknown"


def test_binary_detection(tmp_path: Path):
    text = tmp_path / "a.py"
    text.write_text("print(1)\n")
    assert not is_probably_binary(text)

    blob = tmp_path / "a.bin"
    blob.write_bytes(b"\x00\x01\x02" * 100)
    assert is_probably_binary(blob)


def test_test_file_detection():
    assert is_test_file("tests/test_thing.py")
    assert is_test_file("src/__tests__/x.js")
    assert not is_test_file("src/app.py")
