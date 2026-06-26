#!/bin/bash
set -x

threads=4
# Test
for procs in 1 2; do
    nprocs=$(($procs*$threads))
    ./simfactory/bin/sim create-run test$nprocs --walltime 1:00:00 --testsuite \
        --procs $nprocs --num-threads $threads --ppn-used=$nprocs > "$HOME/${SYSTEM}__${procs}_${threads}.log"
done
