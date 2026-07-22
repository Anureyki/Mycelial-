#!/bin/bash
# crontab_list.sh – List all cron jobs

crontab -l 2>/dev/null || printf "INFO: No cron jobs found.\n"
exit 0
