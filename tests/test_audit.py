import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from api_claim_audit.audit import audit
from api_claim_audit.cli import main

SOURCE = '''def greet(name, *, punctuation="!"):
    return name + punctuation
class Config:
    def __init__(self, name="x"):
        self.name = name
    def run(self):
        pass
VALUE = 1
ANNOTATED: int = 2
async def async_greet(name):
    pass
def flexible(**kwargs):
    pass
def positional(name, /, *, option=1):
    pass
'''


class AuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write('src/pkg/__init__.py', SOURCE)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')

    def document(self, code, language='python', name='README.md'):
        self.write(name, 'Intro\n```' + language + '\n' + code + '\n```\n')

    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                status = main([str(self.root), *args])
            except SystemExit as exc:
                status = exc.code
        return status, out.getvalue(), err.getvalue()

    def test_good_fixture(self):
        self.document('from pkg import greet, Config\npkg.Config.run(...)\ngreet(name="x")')
        status, output, _ = self.run_cli('--json')
        self.assertEqual(status, 0)
        result = json.loads(output)
        self.assertEqual(result['findings'], [])
        self.assertIn({'claim': 'from pkg import greet', 'file': 'README.md'}, result['verified_claims'])

    def test_bad_import_exact(self):
        self.document('from pkg import missing_thing')
        self.assertEqual([x['rule'] for x in audit(self.root)['findings']], ['documented-import-missing'])
        self.assertEqual(self.run_cli()[0], 2)

    def test_bad_module_exact(self):
        self.document('from pkg.nosuch import thing')
        self.assertEqual([x['rule'] for x in audit(self.root)['findings']], ['documented-module-missing'])

    def test_bad_symbols(self):
        self.document('pkg.Nope\npkg.Config.nomethod')
        self.assertEqual([x['rule'] for x in audit(self.root)['findings']], ['documented-symbol-missing'] * 2)

    def test_bad_kwargs_exact(self):
        self.document('from pkg import greet, Config\ngreet(nam="x")\nConfig(timeout=5)')
        self.assertEqual([x['rule'] for x in audit(self.root)['findings']], ['documented-kwarg-unknown'] * 2)

    def test_reexport_signature(self):
        self.write('src/pkg/core.py', SOURCE)
        self.write('src/pkg/__init__.py', 'from .core import greet')
        self.document('from pkg import greet\ngreet(nam="x")')
        self.assertEqual(audit(self.root)['findings'][0]['rule'], 'documented-kwarg-unknown')

    def test_test_only(self):
        self.write('tests/helpers.py', 'def missing_thing(): pass')
        self.document('from pkg import missing_thing')
        self.assertEqual(audit(self.root)['findings'][0]['rule'], 'documented-import-missing')

    def test_ignore(self):
        self.document('from pkg import missing')
        status, output, _ = self.run_cli('--ignore', 'documented-import-missing', '--json')
        self.assertEqual(status, 0)
        self.assertEqual(json.loads(output)['findings'], [])

    def test_high_threshold(self):
        self.document('pkg.greet(nam="x")')
        status, output, _ = self.run_cli('--fail-on', 'HIGH', '--json')
        self.assertEqual(status, 0)
        self.assertEqual(len(json.loads(output)['findings']), 1)

    def test_medium_default(self):
        self.document('pkg.greet(nam="x")')
        self.assertEqual(self.run_cli()[0], 2)

    def test_sorting(self):
        self.document('pkg.Nope\npkg.greet(nam=1)\nfrom pkg import absent')
        self.document('pkg.Other', name='docs/nested/a.md')
        result = audit(self.root)
        self.assertEqual(result['findings'], sorted(result['findings'], key=lambda f: (f['rule'], f['file'], f['line'])))
        self.assertEqual(result, audit(self.root))

    def test_line_numbers(self):
        self.document('pkg.Nope\nfrom pkg import missing')
        found = audit(self.root)['findings']
        self.assertEqual({f['line'] for f in found}, {3, 4})
        self.assertTrue(all(f['file'] == 'README.md' for f in found))

    def test_verbose(self):
        self.document('from pkg import greet')
        self.assertNotIn('VERIFIED', self.run_cli()[1])
        self.assertIn('VERIFIED', self.run_cli('--verbose')[1])

    def test_version(self):
        self.assertEqual(self.run_cli('--version')[:2], (0, '0.1.0\n'))

    def test_invalid_option(self):
        self.assertEqual(self.run_cli('--unknown')[0], 1)

    def test_invalid_threshold(self):
        self.assertEqual(self.run_cli('--fail-on', 'LOW')[0], 1)

    def test_invalid_ignore(self):
        self.assertEqual(self.run_cli('--ignore', 'typo')[0], 1)

    def test_invalid_path(self):
        with self.assertRaises(ValueError):
            audit(self.root / 'absent')

    def test_no_execution(self):
        self.write('src/pkg/__init__.py', 'raise RuntimeError("never execute")\n' + SOURCE)
        self.document('from pkg import greet')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_no_env_read(self):
        (self.root / '.env').write_bytes(b'\xff\x00')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_root_package(self):
        self.write('other/__init__.py', 'def fn(x): pass')
        self.document('other.fn(typo=1)')
        self.assertEqual(audit(self.root)['findings'][0]['rule'], 'documented-kwarg-unknown')

    def test_single_module(self):
        self.write('single.py', 'VALUE = 1')
        self.document('from single import VALUE')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_normalized_project_name_missing(self):
        self.write('pyproject.toml', '[project]\nname = "Absent-Pkg"')
        self.document('import absent_pkg')
        self.assertEqual(audit(self.root)['findings'][0]['rule'], 'documented-module-missing')

    def test_nested_module(self):
        self.write('src/pkg/sub/__init__.py', '')
        self.write('src/pkg/sub/core.py', 'def hello(x): pass')
        self.document('import pkg.sub.core\npkg.sub.core.hello(x=1)')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_test_files_excluded(self):
        for name in ['test_hidden.py', 'hidden_test.py', 'tests/helper.py']:
            self.write('src/pkg/' + name, 'def hidden(): pass')
        self.document('import pkg.test_hidden\nimport pkg.hidden_test\nimport pkg.tests.helper')
        self.assertEqual(len(audit(self.root)['findings']), 3)

    def test_excluded_directories(self):
        for name in ['.venv', 'dist', '.git', '__pycache__', 'x.egg-info', 'node_modules']:
            self.write('src/pkg/' + name + '/bad.py', 'invalid source !!!')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_symlink_not_followed(self):
        (self.root / 'src/pkg/loop').symlink_to(self.root, target_is_directory=True)
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_invalid_source_error(self):
        self.write('src/pkg/bad.py', 'invalid syntax !!!')
        self.assertEqual(self.run_cli()[0], 1)

    def test_inline_only_imports(self):
        self.write('README.md', '`from pkg import absent` and `pkg.Nope`')
        self.assertEqual([f['rule'] for f in audit(self.root)['findings']], ['documented-import-missing'])

    def test_non_docs_ignored(self):
        self.write('other/note.md', '```python\npkg.Nope\n```')
        self.assertEqual(audit(self.root)['status'], 'PASS')

    def test_reexport_cycle(self):
        self.write('src/pkg/a.py', 'from .b import x')
        self.write('src/pkg/b.py', 'from .a import x')
        self.document('from pkg.a import x')
        self.assertEqual(audit(self.root)['status'], 'PASS')


CASES = [
    ('multiline_import', 'from pkg import (\n    greet,\n    Config,\n)', None),
    ('multiline_import_bad', 'from pkg import (\n    absent,\n)', 'documented-import-missing'),
    ('third_party', 'from requests import get\nrequests.get(url="x")', None),
    ('catchall', 'pkg.flexible(arbitrary=1)', None),
    ('no_kwargs', 'pkg.greet()', None),
    ('unknown_call', 'unknown(nam=1)', None),
    ('unresolved_call', 'pkg.unknown(nam=1)', 'documented-symbol-missing'),
    ('import_alias', 'from pkg import greet as hello\nhello(name="x")', None),
    ('import_alias_bad', 'from pkg import greet as hello\nhello(nam="x")', 'documented-kwarg-unknown'),
    ('module_alias', 'import pkg as p\np.greet(nam="x")', 'documented-kwarg-unknown'),
    ('import_module', 'import pkg', None),
    ('missing_import_module', 'import pkg.absent as p', 'documented-module-missing'),
    ('class_symbol', 'pkg.Config', None),
    ('method_exists', 'pkg.Config.run()', None),
    ('assignment', 'pkg.VALUE', None),
    ('annotated_assignment', 'pkg.ANNOTATED', None),
    ('async_signature', 'pkg.async_greet(nam=1)', 'documented-kwarg-unknown'),
    ('positional_only', 'pkg.positional(name="x")', 'documented-kwarg-unknown'),
    ('keyword_only', 'pkg.positional(option=1)', None),
    ('malformed_dotted_import', 'from pkg.nosuch import ???', None),
    ('malformed_import', 'from pkg import ???', None),
    ('star_import', 'from pkg import *', None),
    ('instance_method', 'client.foo(timeout=1)', None),
    ('expanded_kwargs', 'pkg.greet(**options)', None),
    ('multiline_call', 'pkg.greet(\n    nam="x",\n)', 'documented-kwarg-unknown'),
    ('repeated_reference', 'pkg.Nope; pkg.Nope', 'documented-symbol-missing'),
]


def make_case(code, rule):
    def test(self):
        self.document(code)
        result = audit(self.root)
        self.assertEqual(result['status'], 'FAIL' if rule else 'PASS')
        self.assertEqual({f['rule'] for f in result['findings']}, {rule} if rule else set())
    return test


for name, code, rule in CASES:
    setattr(AuditTests, 'test_case_' + name, make_case(code, rule))


def make_markdown(text, count):
    def test(self):
        self.write('README.md', text)
        result = audit(self.root)
        self.assertEqual(len(result['findings']), count)
        if not count:
            self.assertEqual(result['verified_claims'], [])
    return test


for name, text, count in [
    ('prose', 'pkg.Nope is mentioned in prose.', 0),
    ('empty', '# Command line\nUse the CLI.', 0),
    ('py_fence', '```py\npkg.Nope\n```', 1),
    ('bare_fence', '```\npkg.Nope\n```', 1),
    ('other_fence', '```javascript\npkg.Nope\n```', 0),
    ('inline_import', 'Use `from pkg import missing`.', 1),
    ('plain_import', 'from pkg import missing', 0),
    ('inline_third_party', '`from requests import get`', 0),
]:
    setattr(AuditTests, 'test_markdown_' + name, make_markdown(text, count))


if __name__ == '__main__':
    unittest.main()
