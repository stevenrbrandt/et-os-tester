#!/usr/bin/env python3
"""Parse a directory of valgrind --log-file output files and report errors."""
import glob
import os
import re
import sys


def parse_file(path):
    """Return (command, error_count) for one valgrind output file."""
    try:
        with open(path, errors='replace') as f:
            content = f.read()
    except OSError:
        return None, 0

    command = None
    m = re.search(r'^==\d+== Command: (.+)$', content, re.MULTILINE)
    if m:
        command = m.group(1).strip()

    errors = 0
    m = re.search(r'^==\d+== ERROR SUMMARY: (\d+) errors', content, re.MULTILINE)
    if m:
        errors = int(m.group(1))

    return command, errors


def main(valgrind_dir):
    files = sorted(glob.glob(os.path.join(valgrind_dir, '*.out')))

    if not files:
        print('No valgrind output files found.')
        print('Total valgrind errors found: 0')
        return

    total_errors = 0
    failures = []   # (error_count, command, path)

    for path in files:
        command, errors = parse_file(path)
        total_errors += errors
        if errors > 0:
            failures.append((errors, command, path))

    print(f'Scanned {len(files)} valgrind output file(s).')
    print(f'Total valgrind errors found: {total_errors}')

    if not failures:
        print('\nAll tests passed valgrind check — no memory errors detected.')
        return

    print(f'\nTests with errors ({len(failures)}):')
    for errors, command, path in sorted(failures, reverse=True):
        # Show just the parameter file name as the test identifier
        test_id = os.path.basename(command.split()[-1]) if command else os.path.basename(path)
        print(f'  {errors:5d} error(s): {test_id}')
        if command:
            print(f'             {command}')


if __name__ == '__main__':
    directory = sys.argv[1] if len(sys.argv) > 1 else '.'
    main(directory)
