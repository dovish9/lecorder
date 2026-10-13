#!/usr/bin/env python3
"""Portable, model-free test suites. Native/model verification is explicit and separate."""
import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUITES = ('unit', 'integration', 'tools', 'frontend')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('suite', nargs='?', choices=(*SUITES, 'all'), default='all')
    args = parser.parse_args()
    for suite in SUITES if args.suite == 'all' else (args.suite,):
        print(f'\n{suite}', flush=True)
        if suite == 'frontend':
            node = shutil.which('node')
            if not node:
                parser.error('Node.js is required for frontend development tests.')
            command = [node, '--test', *map(str, sorted((ROOT/'tests/frontend').glob('test_*.mjs'))) ]
        else:
            command = [sys.executable, '-X', 'utf8', '-m', 'unittest', 'discover',
                       '-s', f'tests/{suite}', '-t', '.', '-v']
        subprocess.run(command, cwd=ROOT, check=True)


if __name__ == '__main__':
    main()
