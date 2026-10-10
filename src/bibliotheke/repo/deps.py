"""Import statements, found by regular expression and resolved to files in
the repository. Visualisation-grade: a comment that looks like an import
counts, and anything that does not resolve to a file in the tree (the
standard library, third-party packages) is dropped.

``extract(path, text)`` gives each import as a list of candidates to try in
order; ``Index`` knows which paths exist and resolves a candidate against
them. The scanner keeps one ``Index`` in step with the commit it is replaying,
so a file is resolved by the path it had at that time.
"""

from __future__ import annotations

import posixpath
import re
from collections import defaultdict

PYTHON = {"py"}
JAVASCRIPT = {"js", "jsx", "mjs", "cjs", "ts", "tsx", "mts", "cts"}
RUST = {"rs"}
C = {"c", "cc", "cpp", "cxx", "h", "hh", "hpp", "hxx"}
LANGUAGES = PYTHON | JAVASCRIPT | RUST | C

_PY_IMPORT = re.compile(r"^[ \t]*import[ \t]+([\w.]+(?:[ \t]*,[ \t]*[\w.]+)*)", re.M)
_PY_FROM = re.compile(r"^[ \t]*from[ \t]+(\.*[\w.]*)[ \t]+import[ \t]+(\(([^)]*)\)|[^\n#]+)", re.M)
_JS = re.compile(
    r"""(?:\bimport|\bexport)\s+(?:[^'";]*?\s+from\s+)?['"]([^'"\n]+)['"]"""
    r"""|\b(?:require|import)\(\s*['"]([^'"\n]+)['"]\s*\)"""
)
_RS_MOD = re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?mod[ \t]+(\w+)[ \t]*;", re.M)
_RS_USE = re.compile(r"^[ \t]*(?:pub(?:\([^)]*\))?[ \t]+)?use[ \t]+((?:crate|super|self)(?:::\w+)*)", re.M)
_C_INCLUDE = re.compile(r'^[ \t]*#[ \t]*include[ \t]*"([^"\n]+)"', re.M)


def extension(path: str) -> str:
    name = path.rpartition("/")[2]
    return name.rpartition(".")[2].lower() if "." in name.lstrip(".") else ""


def extract(path: str, text: str) -> list[list[str]]:
    """Every import in a source file, each as candidates to resolve in order
    (``from a import b`` may mean the module ``a.b`` or a name in ``a``)."""
    ext = extension(path)
    if ext in PYTHON:
        return _python(text)
    if ext in JAVASCRIPT:
        return [[spec] for spec in _JS.findall(text) for spec in spec if spec]
    if ext in RUST:
        return [[f"mod {name}"] for name in _RS_MOD.findall(text)] + [[spec] for spec in _RS_USE.findall(text)]
    if ext in C:
        return [[spec] for spec in _C_INCLUDE.findall(text)]
    return []


def _python(text: str) -> list[list[str]]:
    found = []
    for group in _PY_IMPORT.findall(text):
        for name in group.split(","):
            name = name.strip()
            parts = name.split(".")
            found.append([".".join(parts[:i]) for i in range(len(parts), 0, -1)])
    for module, names, parenthesised in _PY_FROM.findall(text):
        names = parenthesised if parenthesised else names
        for name in names.split(","):
            name = name.split("#")[0].strip().split(" as ")[0].strip()
            if not name or name == "*":
                found.append([module])
                continue
            joined = f"{module}.{name}" if not module.endswith(".") else f"{module}{name}"
            found.append([joined, module])
    return found


class Index:
    """The files that exist right now, by path, by Python module name and by
    base name, so that an import resolves to the file it meant at that time."""

    def __init__(self) -> None:
        self.paths: set[str] = set()
        self.modules: dict[str, set[str]] = defaultdict(set)
        self.names: dict[str, set[str]] = defaultdict(set)

    def add(self, path: str) -> None:
        self.paths.add(path)
        self.names[path.rpartition("/")[2]].add(path)
        for name in _module_names(path):
            self.modules[name].add(path)

    def remove(self, path: str) -> None:
        self.paths.discard(path)
        self.names[path.rpartition("/")[2]].discard(path)
        for name in _module_names(path):
            self.modules[name].discard(path)

    def resolve(self, importer: str, candidates: list[str]) -> str | None:
        """The path a statement in ``importer`` refers to, or None when it
        is not a file in the repository."""
        ext = extension(importer)
        for spec in candidates:
            if ext in PYTHON:
                found = self._python(importer, spec)
            elif ext in JAVASCRIPT:
                found = self._javascript(importer, spec)
            elif ext in RUST:
                found = self._rust(importer, spec)
            else:
                found = self._c(importer, spec)
            if found and found != importer:
                return found
        return None

    # -- per language ------------------------------------------------------

    def _python(self, importer: str, spec: str) -> str | None:
        if spec.startswith("."):
            dots = len(spec) - len(spec.lstrip("."))
            base = importer.rpartition("/")[0]
            for _ in range(dots - 1):
                base = base.rpartition("/")[0]
            rest = spec[dots:]
            target = posixpath.join(base, *rest.split(".")) if rest else base
            return self._first(f"{target}.py", f"{target}/__init__.py")
        return self._nearest(importer, self.modules.get(spec, ()))

    def _javascript(self, importer: str, spec: str) -> str | None:
        if not spec.startswith("."):
            return None  # a package, not a file here
        target = posixpath.normpath(posixpath.join(importer.rpartition("/")[0], spec))
        stem = target.rpartition(".")[0] if extension(target) in JAVASCRIPT else target
        exts = ("ts", "tsx", "js", "jsx", "mjs", "cjs", "mts", "cts")
        return self._first(target, *(f"{stem}.{e}" for e in exts), *(f"{target}/index.{e}" for e in exts))

    def _rust(self, importer: str, spec: str) -> str | None:
        folder, _, filename = importer.rpartition("/")
        stem = filename[:-3]
        # Where this file's child modules live: beside a mod.rs/lib.rs/main.rs,
        # otherwise in a directory named after the file.
        own = folder if stem in ("mod", "lib", "main") else posixpath.join(folder, stem)
        if spec.startswith("mod "):
            name = spec[4:]
            return self._first(f"{own}/{name}.rs", f"{own}/{name}/mod.rs")
        parts = spec.split("::")
        if parts[0] == "crate":
            base = importer[: importer.index("src/") + 3] if "src/" in importer else folder
            parts = parts[1:]
        elif parts[0] == "self":
            base = own
            parts = parts[1:]
        else:  # super, possibly repeated: each one goes up a module
            base = own
            while parts and parts[0] == "super":
                base = base.rpartition("/")[0]
                parts = parts[1:]
        for depth in range(len(parts), -1, -1):  # the longest prefix that is a module file
            target = posixpath.join(base, *parts[:depth]) if depth else base
            if found := self._first(f"{target}.rs", f"{target}/mod.rs", f"{target}/lib.rs", f"{target}/main.rs"):
                return found
        return None

    def _c(self, importer: str, spec: str) -> str | None:
        local = posixpath.normpath(posixpath.join(importer.rpartition("/")[0], spec))
        if local in self.paths:
            return local
        return self._nearest(importer, [p for p in self.names.get(spec.rpartition("/")[2], ()) if p.endswith(spec)])

    # -- helpers -----------------------------------------------------------

    def _first(self, *paths: str) -> str | None:
        return next((p for p in paths if p in self.paths), None)

    @staticmethod
    def _nearest(importer: str, paths) -> str | None:
        """Of several files answering to a name, the one closest to the importer."""
        best = None
        for path in paths:
            prefix = len(posixpath.commonprefix([importer, path]))
            if best is None or prefix > best[0] or (prefix == best[0] and path < best[1]):
                best = (prefix, path)
        return best[1] if best else None


def _module_names(path: str) -> list[str]:
    """The dotted names a Python file answers to: ``src/pkg/m.py`` is
    ``src.pkg.m``, ``pkg.m`` and ``m``; a package's ``__init__.py`` is the
    package."""
    if extension(path) != "py":
        return []
    parts = path[:-3].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return [".".join(parts[i:]) for i in range(len(parts))] if parts else []
