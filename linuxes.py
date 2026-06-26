#!/usr/bin/env python3
import curses
import hashlib
import os
import re
import subprocess
import threading
import time

DISTROS = ['mint', 'opensuse', 'ubuntu', 'fedora', 'debian', 'rocky']
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CACTUS_TH = os.path.join(SCRIPT_DIR, 'cactus.th')
CACTUS_URL = 'https://bitbucket.org/einsteintoolkit/manifest/raw/master/einsteintoolkit.th'

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
    for name in DISTROS:
        probe(name, current_md5)


def all_checked():
    with _cache_lock:
        return all(_cache.get(n, (None, 'checking'))[1] != 'checking' for n in DISTROS)


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

# Matches both BuildKit ("#N [M/K] RUN ...") and legacy ("Step N/M : RUN ...") headers
_DOCKER_STEP_RE = re.compile(r'(?:#\d+ \[\d+/\d+\]|Step \d+/\d+ :)\s+RUN (.*)')

_STEP_PHASES = [
    (re.compile(r'et-pkg-installer'),   'installing packages'),
    (re.compile(r'zypper|apt-get|yum'), 'installing system packages'),
    (re.compile(r'GetComponents'),      'fetching sources'),
    (re.compile(r'sim setup-silent'),   'configuring simfactory'),
    (re.compile(r'sim build'),          'compiling'),
    (re.compile(r'runtests\.sh'),       'running tests'),
    (re.compile(r'useradd'),            'creating user'),
]

# set -x output from runtests.sh: "+ ./simfactory/bin/sim create-run testN ..."
_SIM_RUN_RE  = re.compile(r'\+.*sim create-run test(\d+)')
# Thorn names during compilation
_THORN_RE    = re.compile(r'(?:Building thorn|Compiling)\s+(\S+)')


def _parse_line(line):
    """Update run phase/detail from a single output line; does not append the line."""
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

    m = _THORN_RE.search(line)
    if m:
        _set_run(detail=m.group(1))


# ── Running a distro ─────────────────────────────────────────────────────────

def _step(cmd, log_fh=None):
    """Run a command, feed output to the status panel and optionally a log file."""
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=SCRIPT_DIR
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
    """Full build/test cycle for one distro. Runs in a background thread."""
    yaml      = f'{os_name}.et.yaml'
    log_path  = os.path.join(SCRIPT_DIR, f'build.{os_name}.log')
    results   = os.path.join(SCRIPT_DIR, 'testsuite_results', 'results')

    _set_run(active=True, distro=os_name, phase='starting build',
             detail='', lines=[], success=None)

    with open(log_path, 'wb') as lf:
        rc = _step(['docker-compose', '-f', yaml, 'build'], lf)
    if rc != 0:
        _set_run(active=False, phase='failed', detail='docker-compose build failed')
        return False
    _telegram(f'built {os_name}')

    _set_run(phase='stopping old container', detail='')
    _step(['docker-compose', '-f', yaml, 'down'])

    _set_run(phase='starting container', detail='')
    if _step(['docker-compose', '-f', yaml, 'up', '-d']) != 0:
        _set_run(active=False, phase='failed', detail='docker-compose up failed')
        return False

    _set_run(phase='waiting for container', detail='')
    time.sleep(5)

    success = True
    for suffix in ['1_4', '2_4']:
        log_name = f'{os_name}__{suffix}.log'
        _set_run(phase='copying logs', detail=log_name)
        src = f'{os_name}.et:/home/etuser/{log_name}'
        dst = os.path.join(results, log_name)
        if _step(['docker', 'cp', src, dst]) != 0:
            success = False
    _telegram(f'copied {os_name}')

    _set_run(phase='stopping container', detail='')
    _step(['docker-compose', '-f', yaml, 'down'])

    label = 'done' if success else 'done (errors copying logs)'
    _set_run(active=False, phase=label, success=success)
    return success


def _launch_distro(os_name, on_done=None):
    def _run():
        ok = run_distro_live(os_name)
        if on_done:
            on_done(os_name, ok)
    threading.Thread(target=_run, daemon=True).start()


def _launch_all(on_done=None):
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


# ── Curses drawing ───────────────────────────────────────────────────────────

def distro_status(os_name, current_md5):
    if current_md5 is None:
        return 'no cactus.th', COLOR_RED
    with _cache_lock:
        entry = _cache.get(os_name)
    if entry is None or entry[1] == 'checking':
        return 'checking...', COLOR_DEFAULT
    _, state = entry
    colors = {
        'current':  COLOR_GREEN,
        'outdated': COLOR_YELLOW,
        'no image': COLOR_RED,
        'error':    COLOR_RED,
    }
    return state, colors.get(state, COLOR_DEFAULT)


def _addstr_clipped(stdscr, row, col, text, attr=curses.A_NORMAL):
    """addstr that silently clips to terminal width."""
    h, w = stdscr.getmaxyx()
    if row >= h - 1 or col >= w:
        return
    available = w - col
    stdscr.addstr(row, col, text[:available], attr)


def draw_menu(stdscr, selected, current_md5):
    stdscr.erase()
    h, w = stdscr.getmaxyx()
    run = get_run()

    row = 0
    title = ' Einstein Toolkit Linux Tester '
    _addstr_clipped(stdscr, row, max(0, (w - len(title)) // 2), title,
                    curses.A_BOLD | curses.A_REVERSE)

    row = 2
    md5_short = (current_md5[:16] + '...') if current_md5 else 'missing'
    _addstr_clipped(stdscr, row, 2, f'cactus.th: {md5_short}')

    row = 4
    _addstr_clipped(stdscr, row, 2, f"  {'Distro':<14} Status", curses.A_UNDERLINE)

    for i, name in enumerate(DISTROS):
        label, color = distro_status(name, current_md5)
        r = 5 + i
        cursor = '> ' if i == selected else '  '
        attr = curses.A_REVERSE if i == selected else curses.A_NORMAL
        _addstr_clipped(stdscr, r, 2, f'{cursor}{name:<14}', attr)
        _addstr_clipped(stdscr, r, 18, f'[{label}]', curses.color_pair(color))

    # ── Status panel ──────────────────────────────────────────────────────
    sep_row = 5 + len(DISTROS) + 1          # one blank line after distro list
    panel_top = sep_row + 1

    if run['phase']:
        sep = '─' * (w - 2)
        _addstr_clipped(stdscr, sep_row, 1, sep, curses.color_pair(COLOR_CYAN))

        # Phase line
        distro_label = f'[{run["distro"]}]  ' if run['distro'] else ''
        phase_str    = run['phase']
        detail_str   = f'  {run["detail"]}' if run['detail'] else ''
        _addstr_clipped(stdscr, panel_top, 2,
                        f'{distro_label}{phase_str}{detail_str}',
                        curses.A_BOLD | curses.color_pair(COLOR_CYAN))

        # Log lines — fill remaining space above footer
        log_top  = panel_top + 1
        log_rows = max(0, h - 2 - log_top)
        lines    = run['lines'][-log_rows:] if log_rows else []
        for j, line in enumerate(lines):
            _addstr_clipped(stdscr, log_top + j, 2, line)

    # ── Footer ────────────────────────────────────────────────────────────
    if run['active']:
        keys = ' (test running — K:kill  Q:quit) '
    else:
        keys = ' Enter:run  U:update cactus.th  R:refresh  A:run all  Q:quit '
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
        current_md5 = md5_file(CACTUS_TH)
        probe(os_name, current_md5)

    while True:
        run = get_run()
        if all_checked() and not run['active']:
            stdscr.timeout(-1)
        else:
            stdscr.timeout(500)

        current_md5 = md5_file(CACTUS_TH)
        draw_menu(stdscr, selected, current_md5)

        key = stdscr.getch()

        if key == curses.KEY_UP:
            selected = (selected - 1) % len(DISTROS)
        elif key == curses.KEY_DOWN:
            selected = (selected + 1) % len(DISTROS)

        elif key in (curses.KEY_ENTER, ord('\n'), ord('\r')):
            if not run['active']:
                _launch_distro(DISTROS[selected], on_done=on_done)
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

        elif key in (ord('r'), ord('R')):
            probe_all(md5_file(CACTUS_TH))
            stdscr.timeout(500)

        elif key in (ord('k'), ord('K')):
            # Best-effort kill: stop any running docker-compose containers
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
