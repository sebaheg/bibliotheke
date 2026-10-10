from bibliotheke.repo.deps import Index, extract


def index(*paths):
    found = Index()
    for path in paths:
        found.add(path)
    return found


def resolved(idx, importer, text):
    return sorted({idx.resolve(importer, c) for c in extract(importer, text)} - {None})


def test_python_imports():
    idx = index("src/pkg/__init__.py", "src/pkg/graph.py", "src/pkg/store.py", "src/pkg/sub/deep.py", "tests/test_graph.py", "setup.py")
    text = """
import os, sys
import pkg.store
from pkg import graph, timeutil   # timeutil does not exist
from pkg.sub import deep as d
from . import store
from .sub.deep import thing
from ..pkg import graph
from typing import (
    Any,
    Iterable,
)
"""
    assert extract("src/pkg/graph.py", "from pkg import (a,\n b)") == [["pkg.a", "pkg"], ["pkg.b", "pkg"]]
    assert extract("x.py", "import a.b.c") == [["a.b.c", "a.b", "a"]]
    assert resolved(idx, "src/pkg/graph.py", text) == ["src/pkg/__init__.py", "src/pkg/store.py", "src/pkg/sub/deep.py"]
    assert resolved(idx, "tests/test_graph.py", "from pkg.graph import Graph\nimport pkg") == ["src/pkg/__init__.py", "src/pkg/graph.py"]
    assert resolved(idx, "src/pkg/graph.py", "from .store import x\nimport json") == ["src/pkg/store.py"]
    assert idx.resolve("src/pkg/graph.py", ["pkg.graph"]) is None  # never itself


def test_python_prefers_the_nearest_of_several_modules():
    idx = index("a/utils.py", "b/utils.py", "b/main.py")
    assert idx.resolve("b/main.py", ["utils"]) == "b/utils.py"
    idx.remove("b/utils.py")
    assert idx.resolve("b/main.py", ["utils"]) == "a/utils.py"


def test_javascript_imports():
    idx = index("src/app.ts", "src/lib/util.ts", "src/lib/index.ts", "src/style.css", "src/data.json")
    text = """
import React from 'react';
import { util } from "./lib/util";
import lib from './lib';
import './style.css';
const data = require("./data.json");
export { x } from './lib/util.js';
const lazy = () => import('./lib/util');
"""
    assert resolved(idx, "src/app.ts", text) == ["src/data.json", "src/lib/index.ts", "src/lib/util.ts", "src/style.css"]
    assert extract("a.js", "import x from 'y'") == [["y"]]


def test_rust_imports():
    idx = index("src/main.rs", "src/graph.rs", "src/store/mod.rs", "src/store/git.rs", "src/view/layout.rs", "src/view.rs")
    assert resolved(idx, "src/main.rs", "mod graph;\nmod store;\nmod view;\nuse std::io;") == ["src/graph.rs", "src/store/mod.rs", "src/view.rs"]
    assert resolved(idx, "src/store/mod.rs", "pub mod git;\nuse crate::graph::Graph;\nuse super::view::layout::Layout;") == [
        "src/graph.rs", "src/store/git.rs", "src/view/layout.rs",
    ]
    assert resolved(idx, "src/view.rs", "pub(crate) mod layout;") == ["src/view/layout.rs"]
    assert resolved(idx, "src/store/git.rs", "use super::*;\nuse self::nothing::X;") == ["src/store/mod.rs"]


def test_c_includes():
    idx = index("src/main.c", "src/util.h", "include/lib/api.h", "other/util.h")
    assert resolved(idx, "src/main.c", '#include <stdio.h>\n#include "util.h"\n# include "lib/api.h"') == ["include/lib/api.h", "src/util.h"]
    assert resolved(idx, "include/lib/api.h", '#include "util.h"') == ["other/util.h"]  # nearest by prefix: tie broken by name


def test_unknown_languages_have_no_imports():
    assert extract("README.md", "import nothing") == []
    assert extract("Makefile", "include x.mk") == []
