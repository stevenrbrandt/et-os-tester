#!/bin/bash
set -x

VALGRIND_DIR="$HOME/valgrind-out"
mkdir -p "$VALGRIND_DIR"

procs=2    # MPI ranks
threads=2  # OpenMP threads per rank
nprocs=$((procs*threads))

# Use make sim-testsuite (not sim create-run --testsuite): simfactory overrides
# CCTK_TESTSUITE_RUN_COMMAND when going through sim create-run, so the variable
# only works when make drives the testsuite directly.
export PROMPT=no
export CCTK_TESTSUITE_RUN_PROCESSORS=$nprocs
export CCTK_TESTSUITE_RUN_COMMAND="mpirun -np ${procs} valgrind --error-exitcode=1 \
    --leak-check=full --track-origins=yes \
    --log-file=${VALGRIND_DIR}/%p.out"

make sim-testsuite 2>&1 \
    | tee "$HOME/${SYSTEM}__${procs}_${threads}.log" || true

python3 "$HOME/summarize-valgrind.py" "$VALGRIND_DIR" \
    | tee "$HOME/${SYSTEM}__valgrind_summary.txt"
