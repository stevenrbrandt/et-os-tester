#!/usr/bin/env python3
import curses
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import threading
import time

DISTROS = ['mint', 'opensuse', 'ubuntu', 'fedora', 'debian', 'rocky', 'alma', 'arch']

# Special tests appear below a separator in the menu.  They build on top of
# existing distro images and have extended status (e.g. memory-error counts).
SPECIAL_TESTS = ['ubuntu-valgrind']

ALL_ENTRIES = DISTROS + SPECIAL_TESTS

SCRIPT_DIR          = os.path.dirname(os.path.abspath(__file__))
CACTUS_TH           = os.path.join(SCRIPT_DIR, 'cactus.th')
CACTUS_URL          = 'https://bitbucket.org/einsteintoolkit/manifest/raw/master/einsteintoolkit.th'
VALGRIND_STATE_FILE = os.path.join(SCRIPT_DIR, '.valgrind_results.json')

COLOR_DEFAULT = 0
COLOR_RED     = 1
COLOR_GREEN   = 2
COLOR_YELLOW  = 3
COLOR_CYAN    = 4

MAX_LOG_LINES = 10


# ── Image probe cache ────────────────────────────────────────────────────────

_cache: dict = {}
_cache_lock = threading.Lock()


def _probe_image(os_name, current_md5):
    image = f'stevenrbrandt/{os_name}.et'
    try:
        r = subprocess.run(
            ['docker', 'image', 'inspect', image],
            capture_output=True, timeout=10
        )
        if r.returncode != 0:
            with _cache_lock:
                _cache[os_name] = (None, 'no image')
            return
        r = subprocess.run(
            ['docker', 'run', '--rm', image, 'md5sum', '/home/etuser/cactus.th'],
            capture_output=True, text=True, timeout=60
        )
        if r.returncode != 0:
            with _cache_lock:
                _cache[os_name] = (None, 'error')
            return
        img_md5 = r.stdout.split()[0]
        status = 'current' if (current_md5 and img_md5 == current_md5) else 'outdated'
        with _cache_lock:
            _cache[os_name] = (img_md5, status)
    except Exception:
        with _cache_lock:
            _cache[os_name] = (None, 'error')


def probe(os_name, current_md5):
    with _cache_lock:
        _cache[os_name] = (None, 'checking')
    threading.Thread(target=_probe_image, args=(os_name, current_md5), daemon=True).start()


def probe_all(current_md5):
    for name in ALL_ENTRIES:
        probe(name, current_md5)


def all_checked():
    with _cache_lock:
        return all(_cache.get(n, (None, 'checking'))[1] != 'checking' for n in ALL_ENTRIES)


# ── Valgrind results state ───────────────────────────────────────────────────

def _load_valgrind_results():
    try:
        with open(VALGRIND_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_valgrind_result(test_name, error_count):
    results = _load_valgrind_results()
    results[test_name] = error_count
    with open(VALGRIND_STATE_FILE, 'w') as f:
        json.dump(results, f, indent=2)


# ── Live run status ──────────────────────────────────────────────────────────

_run: dict = {
    'active':  False,
    'distro':  None,
    'phase':   '',
    'detail':  '',
    'lines':   [],
    'success': None,
}
_run_lock = threading.Lock()


def _set_run(**kwargs):
    with _run_lock:
        _run.update(kwargs)


def _append_line(text):
    s = text.rstrip('\r\n')
    if not s:
        return
    with _run_lock:
        _run['lines'].append(s)
        if len(_run['lines']) > MAX_LOG_LINES:
            _run['lines'].pop(0)


def get_run():
    with _run_lock:
        return dict(_run, lines=list(_run['lines']))


# ── Phase detection from build output ────────────────────────────────────────

_DOCKER_STEP_RE = re.compile(r'(?:#\d+ \[\d+/\d+\]|Step \d+/\d+ :)\s+RUN (.*)')

_STEP_PHASES = [
    (re.compile(r'et-pkg-installer'),        'installing packages'),
    (re.compile(r'zypper|apt-get|yum|dnf'),  'installing system packages'),
    (re.compile(r'GetComponents'),           'fetching sources'),
    (re.compile(r'sim setup-silent'),        'configuring simfactory'),
    (re.compile(r'sim build'),               'compiling'),
    (re.compile(r'runtests-valgrind\.sh'),   'running valgrind tests'),
    (re.compile(r'runtests\.sh'),            'running tests'),
    (re.compile(r'useradd'),                 'creating user'),
]

_SIM_RUN_RE      = re.compile(r'\+.*sim create-run (?:test|valgrind)(\d+)')
_THORN_RE        = re.compile(r'(?:Building thorn|Compiling)\s+(\S+)')
_VALGRIND_ERR_RE = re.compile(r'^==\d+== ERROR SUMMARY: (\d+) errors', re.MULTILINE)
# Simfactory testsuite output lines (visible now that runtests.sh uses tee)
_TEST_RUN_RE     = re.compile(r'Running\s+TEST\s*:?\s*(\S+)', re.IGNORECASE)
_TEST_DONE_RE    = re.compile(r'\b(PASSED|FAILED)\b')


def _parse_line(line):
    m = _DOCKER_STEP_RE.search(line)
    if m:
        cmd = m.group(1)
        for pat, phase in _STEP_PHASES:
            if pat.search(cmd):
                _set_run(phase=phase, detail='')
                return

    m = _SIM_RUN_RE.search(line)
    if m:
        _set_run(detail=f'procs={m.group(1)}')
        return

    m = _VALGRIND_ERR_RE.search(line)
    if m:
        n = int(m.group(1))
        _set_run(detail=f'{n} error(s) so far' if n else 'clean so far')
        return

    m = _TEST_RUN_RE.search(line)
    if m:
        _set_run(detail=m.group(1))
        return

    m = _TEST_DONE_RE.search(line)
    if m:
        with _run_lock:
            prev = _run.get('detail', '')
        _set_run(detail=f'{prev}  [{m.group(1)}]')
        return

    m = _THORN_RE.search(line)
    if m:
        _set_run(detail=m.group(1))


# ── Running a distro / special test ─────────────────────────────────────────

def _step(cmd, log_fh=None):
    env = os.environ.copy()
    env['BUILDKIT_STEP_LOG_MAX_SIZE'] = '-1'
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        cwd=SCRIPT_DIR, env=env
    )
    for raw in proc.stdout:
        text = raw.decode('utf-8', errors='replace')
        _parse_line(text)
        _append_line(text)
        if log_fh:
            log_fh.write(raw)
    proc.wait()
    return proc.returncode


def _telegram(msg):
    try:
        subprocess.run(['telegram-send', msg], check=False)
    except FileNotFoundError:
        pass


def run_distro_live(os_name):
    """Full build/test cycle for one distro or special test."""
    yaml     = f'{os_name}.et.yaml'
    log_path = os.path.join(SCRIPT_DIR, f'build.{os_name}.log')
    results  = os.path.join(SCRIPT_DIR, 'testsuite_results', 'results')
    # Use a per-distro project name so each compose service gets its own
    # network.  Without this, all services share et-tester_default and
    # tearing down one distro while another is running fails with
    # "network has active endpoints".
    dc       = ['docker-compose', '-p', os_name, '-f', yaml]

    _set_run(active=True, distro=os_name, phase='starting build',
             detail='', lines=[], success=None)

    with open(log_path, 'wb') as lf:
        rc = _step(dc + ['build'], lf)
    if rc != 0:
        _set_run(active=False, phase='failed', detail='docker-compose build failed')
        return False
    _telegram(f'built {os_name}')

    _set_run(phase='stopping old container', detail='')
    _step(dc + ['down'])

    _set_run(phase='starting container', detail='')
    if _step(dc + ['up', '-d']) != 0:
        _set_run(active=False, phase='failed', detail='docker-compose up failed')
        return False

    _set_run(phase='waiting for container', detail='')
    time.sleep(5)

    success = True
    # valgrind runs with procs=2 threads=2 → suffix 2_2; normal runs → 1_4 and 2_4
    log_suffixes = ['2_2'] if os_name in SPECIAL_TESTS else ['1_4', '2_4']
    for suffix in log_suffixes:
        log_name = f'{os_name}__{suffix}.log'
        _set_run(phase='copying logs', detail=log_name)
        src = f'{os_name}.et:/home/etuser/{log_name}'
        dst = os.path.join(results, log_name)
        if _step(['docker', 'cp', src, dst]) != 0:
            success = False
    _telegram(f'copied {os_name}')

    # Extra step for valgrind tests: copy summary and record error count
    if os_name in SPECIAL_TESTS:
        summary_name = f'{os_name}__valgrind_summary.txt'
        _set_run(phase='copying valgrind summary', detail='')
        src = f'{os_name}.et:/home/etuser/{summary_name}'
        dst = os.path.join(results, summary_name)
        if _step(['docker', 'cp', src, dst]) == 0:
            _parse_valgrind_summary(os_name, dst)

    _set_run(phase='stopping container', detail='')
    _step(dc + ['down'])

    label = 'done' if success else 'done (errors copying logs)'
    _set_run(active=False, phase=label, success=success)
    return success


def _parse_valgrind_summary(test_name, summary_path):
    """Extract total error count from summary file and persist it."""
    try:
        with open(summary_path) as f:
            content = f.read()
        m = re.search(r'Total valgrind errors found: (\d+)', content)
        if m:
            _save_valgrind_result(test_name, int(m.group(1)))
    except Exception:
        pass


def _launch_distro(os_name, on_done=None):
    def _run():
        ok = run_distro_live(os_name)
        if on_done:
            on_done(os_name, ok)
    threading.Thread(target=_run, daemon=True).start()


def _launch_all(on_done=None):
    """Run all regular distros (not special tests)."""
    def _run():
        for name in DISTROS:
            ok = run_distro_live(name)
            if on_done:
                on_done(name, ok)
    threading.Thread(target=_run, daemon=True).start()


# ── cactus.th helpers ────────────────────────────────────────────────────────

def md5_file(path):
    try:
        h = hashlib.md5()
        with open(path, 'rb') as f:
            h.update(f.read())
        return h.hexdigest()
    except FileNotFoundError:
        return None


def update_cactus_th():
    new_path = CACTUS_TH + '.new'
    subprocess.run(['curl', '-o', new_path, '-kL', CACTUS_URL], check=True)
    new_md5 = md5_file(new_path)
    old_md5 = md5_file(CACTUS_TH)
    if new_md5 != old_md5:
        os.replace(new_path, CACTUS_TH)
        return True, new_md5
    os.unlink(new_path)
    return False, old_md5


# ── Install guide generation ─────────────────────────────────────────────────

def _load_installer():
    """Import et-pkg-installer.py as a module (filename has hyphens)."""
    path = os.path.join(SCRIPT_DIR, 'et-pkg-installer.py')
    spec = importlib.util.spec_from_file_location('et_pkg_installer', path)
    mod  = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fmt_pkg(val):
    """Return (install_list, markdown_cell) for one pkgdict value."""
    if val is None:
        return [], '*(pre-installed or not required)*'
    if isinstance(val, str):
        return [val], f'`{val}`'
    # list of alternatives: each entry is either a str or a list-of-strs
    parts = []
    install = None
    for opt in val:
        if isinstance(opt, list):
            strs = ' '.join(f'`{p}`' for p in opt)
            if install is None:
                install = opt
        else:
            strs = f'`{opt}`'
            if install is None:
                install = [opt]
        parts.append(strs)
    cell = ' *or* '.join(parts)
    return install or [], cell


def generate_install_guide():
    """Write cactus-install-guide.md and return its path."""
    mod = _load_installer()

    # Each entry: (title, pkg_cmd, pkgdict, prereq_lines, notes_markdown)
    SECTIONS = [
        ('Debian / Ubuntu / Linux Mint', 'apt-get',  mod.debk,  [
            'sudo apt-get update',
        ], None),
        ('Fedora',                        'dnf',      mod.redk,  [
        ], None),
        ('Rocky Linux / AlmaLinux',       'dnf',      mod.redk,  [
            '# Enable EPEL and CRB repositories first:',
            'sudo dnf install -y epel-release',
            'sudo dnf config-manager --set-enabled crb',
        ], None),
        ('openSUSE',                      'zypper',   mod.susek, [
        ], None),
        ('Arch Linux',                    'pacman',   mod.archk, [
        ], None),
        ('macOS (Homebrew)',              'brew',     mod.brewk, [
            '# Install Homebrew from https://brew.sh if not already present',
        ], (
            '> **Note — GCC version:** Install `gcc@14` rather than the default `gcc`.\n'
            '> GCC 16 (Homebrew\'s current default as of mid-2025) has an internal\n'
            '> compiler error (ICE) when building `ET_BHaHAHA` with `-O2` and OpenMP\n'
            '> enabled (`#pragma GCC reset_options` inside an OpenMP-outlined function\n'
            '> triggers a segfault in the `evrp` pass of `cc1`).  GCC 14 builds cleanly.\n'
        )),
    ]

    INSTALL_PREFIX = {
        'apt-get': 'sudo apt-get install -y',
        'dnf':     'sudo dnf install -y',
        'zypper':  'sudo zypper install -y',
        'pacman':  'sudo pacman -S --noconfirm',
        'brew':    'brew install',
    }

    out = os.path.join(SCRIPT_DIR, 'cactus-install-guide.md')
    with open(out, 'w') as f:
        f.write('# Cactus / Einstein Toolkit — Package Installation Guide\n\n')
        f.write('Packages required to build and run the Einstein Toolkit,\n')
        f.write('listed per platform.  Generated from `et-pkg-installer.py`.\n\n')

        for title, cmd, pkgdict, prereqs, notes in SECTIONS:
            f.write(f'## {title}\n\n')

            if notes:
                f.write(notes + '\n')

            if prereqs:
                f.write('```bash\n')
                for line in prereqs:
                    f.write(line + '\n')
                f.write('```\n\n')

            # Collect packages for the install command (first alternative)
            to_install = []
            seen = set()
            for key in pkgdict:
                pkgs, _ = _fmt_pkg(pkgdict[key])
                for p in pkgs:
                    if p not in seen:
                        seen.add(p)
                        to_install.append(p)

            if to_install:
                prefix = INSTALL_PREFIX[cmd]
                f.write('```bash\n')
                f.write(f'{prefix} \\\n')
                for i, pkg in enumerate(to_install):
                    cont = ' \\' if i < len(to_install) - 1 else ''
                    f.write(f'    {pkg}{cont}\n')
                f.write('```\n\n')

            # Per-dependency table
            f.write('| Dependency | Package |\n')
            f.write('|------------|---------|\n')
            for key in pkgdict:
                _, cell = _fmt_pkg(pkgdict[key])
                f.write(f'| {key} | {cell} |\n')
            f.write('\n')

    return out


# ── Curses drawing ───────────────────────────────────────────────────────────

def distro_status(os_name, current_md5):
    """Return (label, color_pair) for a distro or special test."""
    if current_md5 is None:
        return 'no cactus.th', COLOR_RED
    with _cache_lock:
        entry = _cache.get(os_name)
    if entry is None or entry[1] == 'checking':
        return 'checking...', COLOR_DEFAULT
    _, state = entry

    base_color = {'current': COLOR_GREEN, 'outdated': COLOR_YELLOW}.get(state, COLOR_RED)

    if os_name in SPECIAL_TESTS and state in ('current', 'outdated'):
        vr = _load_valgrind_results()
        if os_name in vr:
            count = vr[os_name]
            suffix = 'clean' if count == 0 else f'{count} errors'
            color  = COLOR_GREEN if count == 0 else COLOR_RED
            return f'{state} | {suffix}', color

    return state, base_color


def _addstr_clipped(stdscr, row, col, text, attr=curses.A_NORMAL):
    h, w = stdscr.getmaxyx()
    if row >= h - 1 or col >= w:
        return
    stdscr.addstr(row, col, text[:w - col], attr)


# Column layout: cursor(2) + name(18) = col 20 for status bracket
_NAME_COL  = 18
_STATUS_COL = 2 + _NAME_COL


def _draw_entry(stdscr, row, idx, selected, name, current_md5):
    label, color = distro_status(name, current_md5)
    cursor = '> ' if idx == selected else '  '
    attr   = curses.A_REVERSE if idx == selected else curses.A_NORMAL
    _addstr_clipped(stdscr, row, 2, f'{cursor}{name:<{_NAME_COL}}', attr)
    _addstr_clipped(stdscr, row, _STATUS_COL, f'[{label}]', curses.color_pair(color))


def draw_menu(stdscr, selected, current_md5):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    run = get_run()

    # Title
    title = ' Einstein Toolkit Linux Tester '
    _addstr_clipped(stdscr, 0, max(0, (w - len(title)) // 2), title,
                    curses.A_BOLD | curses.A_REVERSE)

    # cactus.th md5
    md5_short = (current_md5[:16] + '...') if current_md5 else 'missing'
    _addstr_clipped(stdscr, 2, 2, f'cactus.th: {md5_short}')

    # Column header
    _addstr_clipped(stdscr, 4, 2,
                    f"  {'Distro':<{_NAME_COL}} Status", curses.A_UNDERLINE)

    # Regular distros
    for i, name in enumerate(DISTROS):
        _draw_entry(stdscr, 5 + i, i, selected, name, current_md5)

    # Separator + special tests
    if SPECIAL_TESTS:
        sep_row = 5 + len(DISTROS) + 1
        sep = '─' * max(0, w - 4)
        label = ' special tests '
        mid = max(0, (w - len(label)) // 2)
        _addstr_clipped(stdscr, sep_row, 2, sep, curses.A_DIM)
        _addstr_clipped(stdscr, sep_row, mid, label, curses.A_DIM)

        for j, name in enumerate(SPECIAL_TESTS):
            idx = len(DISTROS) + j
            _draw_entry(stdscr, sep_row + 1 + j, idx, selected, name, current_md5)

    # Status panel (shown whenever a phase is set)
    first_panel_row = 5 + len(DISTROS) + (3 + len(SPECIAL_TESTS) if SPECIAL_TESTS else 2)
    if run['phase']:
        sep = '─' * max(0, w - 2)
        _addstr_clipped(stdscr, first_panel_row, 1, sep, curses.color_pair(COLOR_CYAN))

        distro_label = f'[{run["distro"]}]  ' if run['distro'] else ''
        detail_str   = f'  {run["detail"]}' if run['detail'] else ''
        _addstr_clipped(stdscr, first_panel_row + 1, 2,
                        f'{distro_label}{run["phase"]}{detail_str}',
                        curses.A_BOLD | curses.color_pair(COLOR_CYAN))

        log_top  = first_panel_row + 2
        log_rows = max(0, h - 2 - log_top)
        for j, line in enumerate(run['lines'][-log_rows:]):
            _addstr_clipped(stdscr, log_top + j, 2, line)

    # Footer
    if run['active']:
        keys = ' (test running — K:kill  Q:quit) '
    else:
        keys = ' Enter:run  U:update cactus.th  R:refresh  A:run all  H:install guide  Q:quit '
    _addstr_clipped(stdscr, h - 1, max(0, (w - len(keys)) // 2), keys, curses.A_DIM)

    stdscr.refresh()


# ── Main loop ────────────────────────────────────────────────────────────────

def main_loop(stdscr):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(COLOR_RED,    curses.COLOR_RED,    -1)
    curses.init_pair(COLOR_GREEN,  curses.COLOR_GREEN,  -1)
    curses.init_pair(COLOR_YELLOW, curses.COLOR_YELLOW, -1)
    curses.init_pair(COLOR_CYAN,   curses.COLOR_CYAN,   -1)

    current_md5 = md5_file(CACTUS_TH)
    probe_all(current_md5)

    selected = 0
    stdscr.timeout(500)

    def on_done(os_name, ok):
        probe(os_name, md5_file(CACTUS_TH))

    n_entries = len(ALL_ENTRIES)

    while True:
        run = get_run()
        stdscr.timeout(-1 if (all_checked() and not run['active']) else 500)

        current_md5 = md5_file(CACTUS_TH)
        draw_menu(stdscr, selected, current_md5)

        key = stdscr.getch()

        if key == curses.KEY_UP:
            selected = (selected - 1) % n_entries
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % n_entries

        elif key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            if not run['active']:
                _launch_distro(ALL_ENTRIES[selected], on_done=on_done)
                stdscr.timeout(500)

        elif key in (ord('a'), ord('A')):
            if not run['active']:
                _launch_all(on_done=on_done)
                stdscr.timeout(500)

        elif key in (ord('u'), ord('U')):
            if not run['active']:
                curses.endwin()
                print('\nUpdating cactus.th...')
                try:
                    updated, md5 = update_cactus_th()
                    if updated:
                        print(f'cactus.th updated  (md5: {md5})')
                    else:
                        print(f'cactus.th already up to date  ({md5[:16]}...)')
                except Exception as e:
                    print(f'Error: {e}')
                print('\nPress Enter to return to menu...', end='', flush=True)
                input()
                probe_all(md5_file(CACTUS_TH))
                stdscr.timeout(500)
                stdscr.refresh()

        elif key in (ord('h'), ord('H')):
            if not run['active']:
                curses.endwin()
                print('\nGenerating install guide...')
                try:
                    path = generate_install_guide()
                    print(f'Written to: {path}')
                except Exception as e:
                    print(f'Error: {e}')
                print('\nPress Enter to return to menu...', end='', flush=True)
                input()
                stdscr.refresh()

        elif key in (ord('r'), ord('R')):
            probe_all(md5_file(CACTUS_TH))
            stdscr.timeout(500)

        elif key in (ord('k'), ord('K')):
            if run['active'] and run['distro']:
                yaml = f'{run["distro"]}.et.yaml'
                subprocess.Popen(
                    ['docker-compose', '-f', yaml, 'down'],
                    cwd=SCRIPT_DIR, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
                )

        elif key in (ord('q'), ord('Q'), 27):
            break


def main():
    os.chdir(SCRIPT_DIR)
    curses.wrapper(main_loop)


if __name__ == '__main__':
    main()
