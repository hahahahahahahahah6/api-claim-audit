# api-claim-audit

Offline CLI that checks a Python project's documented public API against its
source on disk. Version 0.1.0 uses only the Python standard library (Python
3.11+). It never imports or executes project code, accesses the network, or
reads `.env` contents. Reports contain matched claims and rule names, never
unrelated file contents.

## Usage

Install with `pip install .`, then run:

```sh
api-claim-audit .
api-claim-audit /path/to/project --json
api-claim-audit . --fail-on HIGH
api-claim-audit . --ignore documented-import-missing --verbose
api-claim-audit --version
python -m api_claim_audit .
```

The path defaults to `.`. `--ignore RULE,...` drops named rules entirely; for
example, ignore an import rule when documenting a symbol provided only by an
optional extra. Unknown rules and invalid arguments are usage errors.

For a package defining `greet(name)` and `Config.__init__(name="x")`, this
README example verifies an import and a call:

```python
from pkg import greet, Config
greet(name="Ada")
```

Changing the call to `greet(nam="Ada")` reports an unknown keyword. Documenting
`from pkg import missing_thing` reports a missing import. Constructor keyword
arguments are checked against `__init__`; `pkg.Config.run` checks a class method.
Imports with aliases check the original name, and imported aliases resolve
function and constructor calls within the same fenced example.

Human output prints `SEVERITY rule file:line message` for each finding, then a
PASS/FAIL summary. Verified claims print only with `--verbose`. `--json` returns:

```json
{"status":"PASS","findings":[],"verified_claims":[{"claim":"from pkg import greet","file":"README.md"}]}
```

Each finding has `severity`, `rule`, `file`, `line`, and `message`; findings sort
by `(rule, file, line)`. Exit codes are **0** for PASS, **2** for FAIL, and **1**
for usage/input errors. The default failure threshold is MEDIUM. With
`--fail-on HIGH`, MEDIUM findings still print but do not fail.

## Full rule table

| Rule | Severity | Trigger |
| --- | --- | --- |
| `documented-import-missing` | HIGH | `from pkg[.sub] import Name` names a symbol absent from an existing module. Includes the import line and missing symbol in the message. |
| `documented-module-missing` | HIGH | `import pkg.sub` or `from pkg.sub import ...` names a module absent on disk; a missing own top-level package uses this rule too. |
| `documented-symbol-missing` | HIGH | `pkg[.sub].Name` is absent, or `pkg[.sub].Class.method` names a missing method. One finding per missing reference. |
| `documented-kwarg-unknown` | MEDIUM | A resolved function or constructor call passes a keyword its signature does not accept, without a `**kwargs` catch-all. One finding per unknown keyword. Positional-only parameters are not keyword-passable. |
| `verified_claims` | Not a finding | Resolved imports, symbols, and calls appear as claim text plus doc file in JSON; human output shows them with `--verbose`. No claims means PASS with zero verified claims. |

## Scan boundaries

Scans top-level `*.md` (including README.md) and recursive `docs/**/*.md`.
Imports are claims only inside Python (`python`, `py`) or bare backtick fences,
or inline code spans. Dotted references and calls are checked in fences only.
Malformed import syntax is skipped. Plain prose is not a claim.

Own names come from the normalized `[project].name` in pyproject.toml (lowercase,
dashes replaced by underscores), packages containing `__init__.py` under the
root or `src/`, and single Python modules there. Third-party names are ignored.
Module evidence comes from Python files and package initializers. Top-level
functions, classes, assignments, annotated assignments, and explicit
`from ... import ...` re-exports count as symbols. Class methods come from the
class body. Re-exports count in any module, including `__init__.py`.

Tests are never evidence: `tests/`, `test_*.py`, and `*_test.py` are excluded.
The scanner also skips `.venv`, `__pycache__`, `dist`, `.git`, `node_modules`,
and `.egg-info` directories. Symlinks are not followed. Unparseable Python
source produces an input error rather than a misleading missing-symbol report.

## Differentiation

- **readme-claim-audit** audits install names, --flags, links, versions, make
  targets, and `python -m` paths. This tool audits documented importable symbols,
  modules, and call signatures; there is no overlap in rules.
- **apimirage** really imports code at runtime to check whether an API exists,
  including LLM-hallucinated APIs. This tool is static and offline, executes
  nothing, and checks a project's own docs against its own source.
- **pyflakes/ruff** check names used inside code. This checks names promised
  inside documentation.
- **Sphinx autodoc** generates docs from code and cannot drift in that generated
  surface. Hand-written README/docs examples can drift; this audits that drift.
- **dep-claim-audit / privacy-claim-audit** audit dependency and privacy prose.
  This audits API prose only, within the code formatting boundaries above.

## Honest limitations

Static scanning misses dynamic symbols: importlib, module `__getattr__` hooks,
exec/eval-defined names, and symbols added by decorators at runtime. A symbol
that exists but is not actually importable, for example behind a failing
top-level import, still counts as present.

Method checks cover only the `Class.method` dotted form. Instance calls such as
`client.foo()` cannot be resolved statically and are skipped. Inherited methods
and inherited constructor signatures are not inferred. Calls to functions
accepting `**kwargs` skip keyword checks entirely. Unresolved callees are skipped
by the keyword rule. Required arguments, argument values and types, positional
arity, and runtime behavior are not checked. Star re-exports are not expanded.

Claim detection is limited to fenced code blocks and inline code spans in docs;
usage written as plain prose without code formatting is not checked. Inline
spans support import claims only. Third-party imports are ignored by design.
Examples are static evidence, not proof that code runs successfully.

## Development

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
python3 -m build
```

Builds produce an sdist and wheel. Tests use local temporary fixtures and no
network. MIT licensed.
