#!/bin/bash
# Supervisor: keeps box_worker.py running forever; safe to call repeatedly (cron watchdog).
LOG=/workspace/nrsc-shorts/worker.log
exec 9>/tmp/nrsc-worker.lock; flock -n 9 || exit 0
while true; do
  echo "$(date '+%F %T') supervisor: starting worker" >> $LOG
  python3 -u /workspace/nrsc-shorts/nrsc-shorts/tools/box_worker.py >> $LOG 2>&1
  echo "$(date '+%F %T') supervisor: worker exited $?, restarting in 10s" >> $LOG; sleep 10
done
