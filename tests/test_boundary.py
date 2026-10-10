"""The repository scanner (bibliotheke.repo) is meant to become its own
project one day, so the library must not depend on it."""

from pathlib import Path

SRC = Path(__file__).parent.parent / "src" / "bibliotheke"


def test_library_does_not_import_the_repo_package():
    offenders = []
    for path in SRC.glob("*.py"):
        for number, line in enumerate(path.read_text().splitlines(), 1):
            if "bibliotheke.repo" in line and not (path.name == "cli.py" and "import repo" in line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert offenders == [], "only cli.py may refer to bibliotheke.repo, to register its command"
