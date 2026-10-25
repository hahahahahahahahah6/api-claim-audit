"""Static documentation API auditing; project code is never imported."""
import ast
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
import tomllib

RULES = {
    'documented-import-missing': 'HIGH',
    'documented-module-missing': 'HIGH',
    'documented-symbol-missing': 'HIGH',
    'documented-kwarg-unknown': 'MEDIUM',
}
SKIP = {'.venv', '__pycache__', 'dist', '.git', 'node_modules', 'tests'}


def excluded(path):
    return any(p in SKIP or p.endswith('.egg-info') for p in path.parts)


@dataclass
class Symbol:
    keywords: set | None = None
    catchall: bool = False
    methods: set | None = None


@dataclass
class Module:
    symbols: dict = field(default_factory=dict)
    exports: dict = field(default_factory=dict)


def signature(node, methods=None):
    args = node.args
    return Symbol({a.arg for a in args.args + args.kwonlyargs
                   if a.arg not in {'self', 'cls'}}, args.kwarg is not None, methods)


def index_source(root):
    modules, own = {}, set()
    config = root / 'pyproject.toml'
    if config.is_file():
        try:
            name = tomllib.loads(config.read_text(encoding='utf-8')).get('project', {}).get('name')
            if name:
                own.add(name.lower().replace('-', '_'))
        except (ValueError, OSError):
            pass
    for base in (root, root / 'src'):
        if not base.is_dir():
            continue
        for entry in sorted(base.iterdir()):
            if entry.is_symlink() or excluded(entry.relative_to(root)):
                continue
            if entry.is_file() and entry.suffix == '.py':
                files = [entry]
                package = entry.stem
            elif entry.is_dir() and (entry / '__init__.py').is_file():
                package = entry.name
                files = []
                for directory, dirs, names in os.walk(entry, followlinks=False):
                    dirs[:] = sorted(d for d in dirs if not excluded(Path(d))
                                     and not (Path(directory) / d).is_symlink())
                    files.extend(Path(directory) / n for n in sorted(names) if n.endswith('.py'))
            else:
                continue
            if package.startswith('test_') or package.endswith('_test'):
                continue
            own.add(package)
            for path in files:
                if path.is_symlink() or path.stem.startswith('test_') or path.stem.endswith('_test'):
                    continue
                parts = list(path.relative_to(base).with_suffix('').parts)
                is_package = parts[-1] == '__init__'
                if is_package:
                    parts.pop()
                name = '.'.join(parts)
                module = Module()
                modules[name] = module
                try:
                    tree = ast.parse(path.read_text(encoding='utf-8'))
                except (SyntaxError, UnicodeError) as exc:
                    raise ValueError('Cannot parse Python source: ' + str(path.relative_to(root))) from exc
                for node in tree.body:
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        module.symbols[node.name] = signature(node)
                    elif isinstance(node, ast.ClassDef):
                        methods = {n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
                        init = next((n for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == '__init__'), None)
                        module.symbols[node.name] = signature(init, methods) if init else Symbol(set(), False, methods)
                    elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                        for target in targets:
                            for item in ast.walk(target):
                                if isinstance(item, ast.Name):
                                    module.symbols[item.id] = Symbol()
                    elif isinstance(node, ast.ImportFrom):
                        parent = parts if is_package else parts[:-1]
                        origin = '.'.join(parent[:len(parent) - node.level + 1]) if node.level else ''
                        origin = '.'.join(p for p in (origin, node.module) if p)
                        for alias in node.names:
                            if alias.name != '*':
                                local = alias.asname or alias.name
                                module.symbols[local] = Symbol()
                                module.exports[local] = (origin, alias.name)
    return own, modules


def code_regions(text):
    """Yield (text, first line, is fenced); preserve physical line numbers."""
    lines = text.splitlines()
    start, block, active, accepted, fence = 0, [], False, False, 0
    for number, line in enumerate(lines, 1):
        match = re.match(r'^\s*(`{3,})(.*)$', line)
        if match:
            ticks, info = match.groups()
            if not active:
                active, accepted, fence = True, info.strip() in {'', 'py', 'python'}, len(ticks)
                start, block = number + 1, []
            elif len(ticks) >= fence and not info.strip():
                if accepted:
                    yield '\n'.join(block), start, True
                active = False
            elif accepted:
                block.append(line)
        elif active:
            if accepted:
                block.append(line)
        else:
            for match in re.finditer(r'(?<!`)(`+)(?!`)(.+?)(?<!`)\1(?!`)', line):
                yield match.group(2), number, False
    if active and accepted:
        yield '\n'.join(block), start, True


def audit(path='.', fail_on='MEDIUM', ignore=()):
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError('Project path must be a directory')
    if fail_on not in {'HIGH', 'MEDIUM'}:
        raise ValueError('Invalid severity threshold')
    unknown = set(ignore) - RULES.keys()
    if unknown:
        raise ValueError('Unknown ignored rule: ' + ', '.join(sorted(unknown)))
    own, modules = index_source(root)
    findings, verified = [], []

    def resolve(name, seen=None):
        seen = set() if seen is None else seen
        if name in seen:
            return None
        seen.add(name)
        mod, _, symbol = name.rpartition('.')
        if mod in modules and symbol in modules[mod].symbols:
            record = modules[mod]
            if symbol in record.exports:
                origin, original = record.exports[symbol]
                return resolve(origin + '.' + original, seen) or record.symbols[symbol]
            return record.symbols[symbol]
        return None

    docs = set(root.glob('*.md'))
    docs_root = root / 'docs'
    if docs_root.is_dir() and not docs_root.is_symlink():
        for directory, dirs, names in os.walk(docs_root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not excluded(Path(d)) and not (Path(directory) / d).is_symlink())
            docs.update(Path(directory) / n for n in names if n.endswith('.md'))
    for doc in sorted(docs):
        if doc.is_symlink() or excluded(doc.relative_to(root)):
            continue
        filename = doc.relative_to(root).as_posix()
        def good(claim):
            item = {'claim': claim, 'file': filename}
            if item not in verified:
                verified.append(item)
        def bad(rule, line, message):
            if rule not in ignore:
                findings.append(dict(severity=RULES[rule], rule=rule, file=filename, line=line, message=message))
        for code, start, fenced in code_regions(doc.read_text(encoding='utf-8')):
            aliases, import_lines = {}, set()
            # Imports are parsed independently so a shell prompt or incomplete example
            # elsewhere in the fence cannot conceal a valid import claim.
            lines = code.splitlines()
            offset = 0
            imports = []
            while offset < len(lines):
                line = lines[offset]
                if not re.match(r'^\s*(from\s|import\s)', line):
                    offset += 1
                    continue
                end = offset + 1
                while True:
                    candidate = '\n'.join(lines[offset:end])
                    try:
                        tree = ast.parse(candidate.strip())
                    except SyntaxError:
                        # Continue only an explicitly continued import, not
                        # arbitrary subsequent example lines.
                        if end < len(lines) and (candidate.rstrip().endswith('\\') or
                                                 candidate.count('(') > candidate.count(')')):
                            end += 1
                            continue
                        tree = None
                    break
                import_lines.update(range(offset + 1, end + 1))
                if tree is not None and tree.body and all(isinstance(n, (ast.Import, ast.ImportFrom)) for n in tree.body):
                    imports.append((offset, candidate, tree))
                offset = end
            for offset, line, tree in imports:
                line = ' '.join(line.split())
                for node in tree.body:
                    if isinstance(node, ast.ImportFrom):
                        module = node.module or ''
                        if node.level or module.split('.')[0] not in own:
                            continue
                        if module not in modules:
                            bad('documented-module-missing', start + offset, line.strip() + ': missing module ' + module)
                            continue
                        for alias in node.names:
                            if alias.name == '*':
                                continue
                            aliases[alias.asname or alias.name] = module + '.' + alias.name
                            if alias.name not in modules[module].symbols:
                                bad('documented-import-missing', start + offset, line.strip() + ': missing symbol ' + alias.name)
                            else:
                                good('from ' + module + ' import ' + alias.name)
                    else:
                        for alias in node.names:
                            if alias.name.split('.')[0] not in own:
                                continue
                            aliases[alias.asname or alias.name.split('.')[0]] = alias.name if alias.asname else alias.name.split('.')[0]
                            if alias.name not in modules:
                                bad('documented-module-missing', start + offset, line.strip() + ': missing module ' + alias.name)
                            else:
                                good('import ' + alias.name)
            if not fenced:
                continue
            for offset, line in enumerate(code.splitlines()):
                if offset + 1 in import_lines:
                    continue
                for match in re.finditer(r'\b[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*)+', line):
                    reference = match.group()
                    parts = reference.split('.')
                    if parts[0] not in own:
                        continue
                    valid = reference in modules or resolve(reference) is not None
                    if not valid and len(parts) >= 3:
                        cls = resolve('.'.join(parts[:-1]))
                        valid = cls is not None and cls.methods is not None and parts[-1] in cls.methods
                    if valid:
                        good(reference)
                    else:
                        bad('documented-symbol-missing', start + offset, 'Missing documented reference ' + reference)
            try:
                tree = ast.parse(code)
            except SyntaxError:
                # Recover individually valid lines in incomplete snippets.
                tree = ast.Module(body=[], type_ignores=[])
                for offset, line in enumerate(code.splitlines()):
                    try:
                        part = ast.parse(line.strip())
                    except SyntaxError:
                        continue
                    ast.increment_lineno(part, offset)
                    tree.body.extend(part.body)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                def dotted(expr):
                    if isinstance(expr, ast.Name):
                        return expr.id
                    if isinstance(expr, ast.Attribute):
                        parent = dotted(expr.value)
                        return parent + '.' + expr.attr if parent else ''
                    return ''
                callee = dotted(node.func)
                first, dot, tail = callee.partition('.')
                expanded = aliases.get(first, first) + (dot + tail if dot else '')
                if expanded.split('.')[0] not in own:
                    continue
                symbol = resolve(expanded)
                if symbol is None or symbol.keywords is None:
                    continue
                invalid = [] if symbol.catchall else [k.arg for k in node.keywords if k.arg is not None and k.arg not in symbol.keywords]
                for keyword in invalid:
                    bad('documented-kwarg-unknown', start + node.lineno - 1, callee + ': unknown keyword ' + keyword)
                if not invalid:
                    good(ast.get_source_segment(code, node) or callee + '(...)')
    findings.sort(key=lambda f: (f['rule'], f['file'], f['line']))
    verified.sort(key=lambda v: (v['file'], v['claim']))
    failed = any(f['severity'] == 'HIGH' or fail_on == 'MEDIUM' for f in findings)
    return {'status': 'FAIL' if failed else 'PASS', 'findings': findings, 'verified_claims': verified}
