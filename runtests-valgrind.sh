#!/bin/bash
set -x

VALGRIND_DIR="$HOME/valgrind-out"
mkdir -p "$VALGRIND_DIR"

# Run tests through valgrind.  Single-MPI-proc (pure OpenMP) to avoid
# the noise that MPI's own allocator generates under valgrind.
threads=4
nprocs=$threads

export CCTK_TESTSUITE_RUN_COMMAND="valgrind --error-exitcode=1 \
    --leak-check=full --track-origins=yes \
    --log-file=${VALGRIND_DIR}/%p.out"

./simfactory/bin/sim create-run valgrind${nprocs} --walltime 8:00:00 --testsuite \
    --procs $nprocs --num-threads $threads --ppn-used=$nprocs \
    > "$HOME/${SYSTEM}__1_${threads}.log" 2>&1 || true

# Collect and summarise memory errors; output goes to summary file
# (and is also printed so it appears in the docker build log).
python3 "$HOME/summarize-valgrind.py" "$VALGRIND_DIR" \
    | tee "$HOME/${SYSTEM}__valgrind_summary.txt"
