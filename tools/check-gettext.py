#!/usr/bin/env python3
"""Fail if any module that imports gettext's `_` also rebinds it.

`_` is a conventional throwaway name in Python, so this is an easy mistake to
make and a silent one to ship: the module imports fine and only explodes at the
moment the shadowed function is called.
"""
import ast
import pathlib
import sys

SRC = pathlib.Path(__file__).parent / "src" / "adbshot"
problems = []

for path in sorted(SRC.glob("*.py")):
    tree = ast.parse(path.read_text())
    uses_gettext = any(
        isinstance(n, ast.ImportFrom) and n.module == "i18n"
        and any(a.name == "_" for a in n.names)
        for n in ast.walk(tree)
    )
    if not uses_gettext:
        continue
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, (ast.Assign,)):
            targets = node.targets
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            targets = [node.target]
        elif isinstance(node, ast.withitem) and node.optional_vars:
            targets = [node.optional_vars]
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            args = node.args
            for a in (*args.posonlyargs, *args.args, *args.kwonlyargs,
                      args.vararg, args.kwarg):
                if a is not None and a.arg == "_":
                    problems.append(f"{path.name}:{node.lineno}: `_` used as a parameter")
            continue
        for t in targets:
            for sub in ast.walk(t):
                if isinstance(sub, ast.Name) and sub.id == "_":
                    problems.append(f"{path.name}:{node.lineno}: `_` rebound as a variable")

if problems:
    print("gettext `_` is shadowed:")
    for p in problems:
        print("  " + p)
    sys.exit(1)
print("ok: no module shadows gettext `_`")
