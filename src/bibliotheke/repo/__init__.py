"""A git repository as a graph: files, directories and authors, with every
commit as a dated edge, so the graph can be viewed at any point in the
repository's history.

This package only consumes bibliotheke's public API; nothing in bibliotheke
depends on it beyond the one command registered in the CLI.
"""

from bibliotheke.repo.scan import MARKER, Scan, scan

__all__ = ["MARKER", "Scan", "scan"]
