"""How `bib` prints tables: one row per line, always.

A table in the terminal is a list to scan, not a place to read. A cell that
does not fit the line ends in an ellipsis instead of wrapping onto a second
line, so every row is one line and the eye can run down a column; the full
text is one `bib show` away.

When a line is too short for everything, the free-text column gives way
(a name, a description, a value), never the ids, types and dates beside it:
those are what the reader is scanning for."""

from __future__ import annotations

from typing import Any

from rich.table import Table as RichTable

# Columns that hold prose or long names: these take whatever width is left
# after the short columns have theirs, and are the ones cut short.
FLEX = {"name", "description", "value", "detail", "members", "subject", "model change", "problems", "to", "from"}


class Table(RichTable):
    def add_column(self, header: Any = "", *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("no_wrap", True)
        kwargs.setdefault("overflow", "ellipsis")
        if "ratio" not in kwargs and str(header).strip().lower() in FLEX:
            kwargs["ratio"] = 1
            self.expand = True  # the flexible column fills the line; the others keep their content width
        super().add_column(header, *args, **kwargs)
