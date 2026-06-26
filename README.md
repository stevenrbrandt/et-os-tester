# Einstein Toolkit Linux Compatibility Tester

A test harness that builds and runs the [Einstein Toolkit](https://einsteintoolkit.org)
on a matrix of Linux distributions using Docker, with a curses-based TUI for interactive
use and a GitHub Actions workflow for macOS.

## Tested platforms

| Distro | Docker image |
|--------|-------------|
| Ubuntu | `ubuntu.et.docker` |
| Debian | `debian.et.docker` |
| Linux Mint | `mint.et.docker` |
| Fedora | `fedora.et.docker` |
| openSUSE | `opensuse.et.docker` |
| Rocky Linux | `rocky.et.docker` |
| AlmaLinux | `alma.et.docker` |
| Arch Linux | `arch.et.docker` |
| macOS (Apple Silicon) | GitHub Actions — `.github/workflows/macos-et.yml` |

### Special tests

| Test | Description |
|------|-------------|
| `ubuntu-valgrind` | Runs the ET test suite through Valgrind and reports memory errors |

## Prerequisites

- Docker with BuildKit enabled
- `docker-compose` (v1 or v2)
- Python 3.6+ with the `curses` module (standard library)
- A copy of `cactus.th` in the repo root — press **U** inside the TUI to download the
  current thornlist from the ET manifest, or run:
  ```
  curl -kLO https://bitbucket.org/einsteintoolkit/manifest/raw/master/einsteintoolkit.th
  cp einsteintoolkit.th cactus.th
  ```

## Quick start

```bash
# Launch the interactive menu
python3 linuxes.py
```

### Key bindings

| Key | Action |
|-----|--------|
| `↑` / `↓` | Move selection |
| `Enter` | Build and test selected distro |
| `A` | Run all standard distros in sequence |
| `U` | Download the latest `cactus.th` from the ET manifest |
| `R` | Re-probe all images for their current status |
| `H` | Write `cactus-install-guide.md` — per-distro package install guide |
| `K` | Kill the currently running build |
| `Q` / `Esc` | Quit |

### Status column

| Status | Meaning |
|--------|---------|
| `current` | The image's `cactus.th` matches the local file — already up to date |
| `outdated` | The image was built against an older thornlist |
| `no image` | Image has never been built |
| `checking…` | Background probe in progress |
| `current \| clean` | (valgrind) Up to date, no memory errors found |
| `current \| N errors` | (valgrind) Up to date, N memory errors detected |

## Shell runner

`linuxes.sh` is a non-interactive alternative that loops over all standard distros:

```bash
bash linuxes.sh
```

## How the Docker images work

Each distro has a `<name>.et.docker` + `<name>.et.yaml` pair.  The Dockerfile:

1. Installs system packages via `et-pkg-installer.py` (supports `apt-get`, `dnf`/`yum`,
   `zypper`, `pacman`, and `brew`)
2. Creates an `etuser` account
3. Downloads `GetComponents` and fetches the ET source tree
4. Builds via `simfactory`
5. Runs the test suite with `runtests.sh`

The final image contains a fully built and tested ET installation, allowing subsequent
runs to re-run tests without recompiling.

Test runners use `tee` rather than plain redirection so build output streams live
through the Docker log (visible in the TUI status panel and in `docker-compose build`
output) and is also saved to a log file inside the container.  `BUILDKIT_STEP_LOG_MAX_SIZE`
is set to `-1` to disable BuildKit's default 2 MiB per-step log cap.

## macOS (GitHub Actions)

The workflow at `.github/workflows/macos-et.yml` runs on an Apple Silicon (`macos-14`)
GitHub-hosted runner.  It is **manual-only**: go to **Actions → macOS Einstein Toolkit →
Run workflow** to trigger it.  Logs are uploaded as a workflow artifact after each run.

> **GCC version:** The workflow pins to `gcc@14`.  GCC 16 (Homebrew's current default
> as of mid-2025) has an internal compiler error when building `ET_BHaHAHA` with
> `-O2` and OpenMP enabled.  GCC 14 builds cleanly.

## Valgrind test

The `ubuntu-valgrind` special test builds on top of the `stevenrbrandt/ubuntu.et` image,
installs Valgrind, and re-runs the test suite with:

```bash
CCTK_TESTSUITE_RUN_COMMAND="mpirun -np 2 valgrind --leak-check=full --track-origins=yes ..."
```

Tests run with 2 MPI ranks × 2 OpenMP threads each (4 cores total) so both MPI and
OpenMP code paths are exercised under Valgrind.  A summary of memory errors (parsed by
`summarize-valgrind.py`) is stored in
`testsuite_results/ubuntu-valgrind__valgrind_summary.txt` and the error count is shown
in the TUI status column.

## Install guide

Press **H** in the TUI to generate `cactus-install-guide.md` — a per-platform reference
listing the packages needed to build the ET, including copy-pasteable install commands
and a dependency table.  The file is generated from the package dictionaries in
`et-pkg-installer.py` and includes platform-specific notes (e.g. the GCC 16 caveat for
macOS, EPEL/CRB setup for Rocky Linux and AlmaLinux).

## Repository layout

```
linuxes.py                  TUI front-end
linuxes.sh                  Shell batch runner
et-pkg-installer.py         Cross-distro package installer
runtests.sh                 In-container test runner (uses tee)
runtests-valgrind.sh        Valgrind test runner (MPI + OpenMP, uses tee)
summarize-valgrind.py       Valgrind output parser
<name>.et.docker            Per-distro Dockerfile
<name>.et.yaml              Per-distro docker-compose file
.github/workflows/
  macos-et.yml              macOS GitHub Actions workflow (manual, gcc@14)
```

`cactus-install-guide.md` is generated by the **H** key and is not tracked by git.

## License

This project is licensed under the
[GNU Lesser General Public License v2.1 or later](LICENSE)
(SPDX: `LGPL-2.1-or-later`), the same license used by the
[Cactus Computational Toolkit](https://cactuscode.org) at the core of the Einstein Toolkit.
