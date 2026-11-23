from __future__ import annotations

from pathlib import Path

EXT_LANGUAGE: dict[str, str] = {
    ".py": "Python", ".pyi": "Python", ".ipynb": "Jupyter",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript", ".cjs": "JavaScript",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".java": "Java", ".kt": "Kotlin", ".kts": "Kotlin", ".scala": "Scala",
    ".go": "Go", ".rs": "Rust", ".rb": "Ruby", ".php": "PHP",
    ".c": "C", ".h": "C", ".cpp": "C++", ".cc": "C++", ".cxx": "C++",
    ".hpp": "C++", ".hh": "C++", ".cs": "C#", ".swift": "Swift", ".m": "Objective-C",
    ".sh": "Shell", ".bash": "Shell", ".zsh": "Shell", ".ps1": "PowerShell",
    ".sql": "SQL", ".r": "R", ".jl": "Julia", ".dart": "Dart", ".lua": "Lua",
    ".ex": "Elixir", ".exs": "Elixir", ".erl": "Erlang", ".clj": "Clojure",
    ".hs": "Haskell", ".ml": "OCaml", ".vue": "Vue", ".svelte": "Svelte",
    ".html": "HTML", ".htm": "HTML", ".css": "CSS", ".scss": "SCSS",
    ".sass": "Sass", ".less": "Less",
    ".json": "JSON", ".yaml": "YAML", ".yml": "YAML", ".toml": "TOML",
    ".xml": "XML", ".ini": "INI", ".env": "DotEnv",
    ".md": "Markdown", ".rst": "reStructuredText", ".txt": "Text",
    ".tf": "Terraform", ".hcl": "HCL", ".proto": "Protobuf", ".graphql": "GraphQL",
    ".gradle": "Gradle", ".cmake": "CMake", ".mk": "Make",
}

FILENAME_LANGUAGE: dict[str, str] = {
    "dockerfile": "Dockerfile", "makefile": "Make", "rakefile": "Ruby",
    "gemfile": "Ruby", "procfile": "Procfile", "jenkinsfile": "Groovy",
    "cmakelists.txt": "CMake", "vagrantfile": "Ruby",
}

# Files that carry project identity — the profiler reads these first.
MANIFESTS: set[str] = {
    "package.json", "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt",
    "pipfile", "poetry.lock", "go.mod", "cargo.toml", "pom.xml", "build.gradle",
    "build.gradle.kts", "composer.json", "gemfile", "mix.exs", "pubspec.yaml",
    "csproj", "deno.json", "bun.lockb", "dockerfile", "docker-compose.yml",
    "docker-compose.yaml", "readme.md", "readme.rst", "readme.txt",
    "makefile", "cmakelists.txt", "tsconfig.json", ".tool-versions",
}

# Never sent to the model, never scored.
SKIP_DIRS: set[str] = {
    ".git", ".hg", ".svn", "node_modules", "__pycache__", ".venv", "venv",
    "env", ".env.d", "dist", "build", "out", "target", ".next", ".nuxt",
    ".svelte-kit", ".turbo", ".parcel-cache", "coverage", ".nyc_output",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".gradle",
    ".idea", ".vscode", "vendor", "bower_components", ".terraform",
    "site-packages", "eggs", ".eggs", "htmlcov", ".cache", "tmp", ".DS_Store",
}

BINARY_EXTS: set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".svg", ".avif",
    ".pdf", ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".exe", ".dll", ".so", ".dylib", ".bin", ".o", ".a", ".class", ".pyc", ".pyd",
    ".mp3", ".mp4", ".avi", ".mov", ".wav", ".flac", ".ogg", ".webm",
    ".ttf", ".otf", ".woff", ".woff2", ".eot",
    ".db", ".sqlite", ".sqlite3", ".parquet", ".pkl", ".npy", ".h5", ".onnx",
    ".lock", ".map", ".min.js", ".min.css",
}

TEST_HINTS = ("test", "spec", "__tests__", "e2e", "fixtures", "conftest")


def detect_language(path: Path) -> str:
    name = path.name.lower()
    if name in FILENAME_LANGUAGE:
        return FILENAME_LANGUAGE[name]
    if name.startswith("dockerfile"):
        return "Dockerfile"
    return EXT_LANGUAGE.get(path.suffix.lower(), "Unknown")


def is_probably_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTS:
        return True
    try:
        with path.open("rb") as fh:
            chunk = fh.read(2048)
    except OSError:
        return True
    if b"\x00" in chunk:
        return True
    if not chunk:
        return False
    # High ratio of non-text bytes => binary
    printable = sum(1 for b in chunk if 32 <= b < 127 or b in (9, 10, 13))
    return printable / len(chunk) < 0.75


def is_manifest(path: Path) -> bool:
    return path.name.lower() in MANIFESTS


def is_test_file(rel_path: str) -> bool:
    low = rel_path.lower()
    return any(h in low for h in TEST_HINTS)


def is_generated(rel_path: str) -> bool:
    low = rel_path.lower()
    return any(
        m in low
        for m in (".min.", ".generated.", "_pb2.", ".pb.go", "-lock.", ".lock",
                  "migrations/", "__generated__", ".d.ts")
    )
