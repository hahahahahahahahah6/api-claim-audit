"""Command line interface."""
import argparse
import json
import sys
from . import __version__
from .audit import audit


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        self.exit(1, self.prog + ': error: ' + message + '\n')


def main(argv=None):
    parser = Parser(prog='api-claim-audit', description='Audit documented Python API claims without executing project code.')
    parser.add_argument('path', nargs='?', default='.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--fail-on', choices=['HIGH', 'MEDIUM'], default='MEDIUM')
    parser.add_argument('--ignore', default='', metavar='RULE,...')
    parser.add_argument('--verbose', action='store_true')
    parser.add_argument('--version', action='version', version=__version__)
    args = parser.parse_args(argv)
    try:
        result = audit(args.path, args.fail_on, [r.strip() for r in args.ignore.split(',') if r.strip()])
    except (ValueError, OSError, UnicodeError) as exc:
        parser.error(str(exc))
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        for item in result['findings']:
            print('{severity} {rule} {file}:{line} {message}'.format(**item))
        if args.verbose:
            for item in result['verified_claims']:
                print('VERIFIED {file} {claim}'.format(**item))
        print(result['status'] + ': ' + str(len(result['findings'])) + ' finding(s)')
    return 2 if result['status'] == 'FAIL' else 0
