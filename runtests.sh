#!/bin/bash
set -x

threads=4

# Awk filter: pass all lines through unchanged, but:
# - track the currently-running test so crashes can be attributed to it
# - on any failure indicator (Failure:, Segmentation fault, Aborted, etc.)
#   print an immediate >>> banner with the log path
# - reprint all failures as a summary block at the very end
failure_filter() {
    local runname="$1"
    awk -v runname="$runname" '
    function record(msg, logpath,    entry) {
        entry = msg "\n    Log: " logpath
        failures[++n] = entry
        print ""
        print ">>> " msg
        print "    Log: " logpath
        print ""
    }

    # Track the test currently being run so we can attribute crashes
    /[Rr]unning test/ {
        cur_test = ""; cur_thorn = ""
        for (i = 1; i <= NF; i++) {
            if ($i == "test"  && i+1 <= NF) cur_test  = $(i+1)
            if ($i == "thorn" && i+1 <= NF) cur_thorn = $(i+1)
        }
        cur_log = (cur_thorn != "" && cur_test != "") \
            ? "SIMULATIONS/" runname "/output-0000/TEST/" cur_thorn "/" cur_test \
            : ""
        print; next
    }

    # Explicit testsuite failure line:
    # "  Failure: N files differ in test TESTNAME of thorn ARR/THORN"
    /Failure:/ {
        testname = ""; thorn = ""
        for (i = 1; i <= NF; i++) {
            if ($i == "test"  && i+1 <= NF) testname = $(i+1)
            if ($i == "thorn" && i+1 <= NF) thorn    = $(i+1)
        }
        if (testname == "") testname = cur_test
        if (thorn    == "") thorn    = cur_thorn
        logpath = (thorn != "" && testname != "") \
            ? "SIMULATIONS/" runname "/output-0000/TEST/" thorn "/" testname \
            : (cur_log != "" ? cur_log : "(unknown)")
        record($0, logpath)
        next
    }

    # Crash / signal — attribute to the current test
    /[Ss]egmentation fault|[Aa]borted|[Kk]illed/ {
        msg = $0
        if (cur_test != "")
            msg = msg " (in test " cur_test " of thorn " cur_thorn ")"
        logpath = cur_log != "" ? cur_log : "(unknown)"
        record(msg, logpath)
        next
    }

    { print }

    END {
        if (n == 0) { print "\nAll tests passed."; exit }
        print ""
        print "========================================================"
        print "FAILED TESTS (" n "):"
        print "========================================================"
        for (i = 1; i <= n; i++) {
            print failures[i]
            print ""
        }
        print "========================================================"
    }
    '
}

for procs in 1 2; do
    nprocs=$(($procs*$threads))
    runname="test${nprocs}"
    ./simfactory/bin/sim create-run $runname --walltime 1:00:00 --testsuite \
        --procs $nprocs --num-threads $threads --ppn-used=$nprocs 2>&1 \
        | tee "$HOME/${SYSTEM}__${procs}_${threads}.log" \
        | failure_filter "$runname"
done
