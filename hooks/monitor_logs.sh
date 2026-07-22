#!/bin/bash
# monitor_logs.sh – Check system logs for critical events
# Usage: monitor_logs.sh [lines] [filter]

LINES="${1:-50}"
FILTER="${2:-ERROR|FAIL|CRITICAL|docker|pihole}"

# Use journalctl to fetch recent logs
sudo journalctl -n "$LINES" --no-pager | grep -E "$FILTER" > /tmp/mycelial_log_scan.txt

# Count matches
COUNT=$(wc -l < /tmp/mycelial_log_scan.txt)

if [[ "$COUNT" -gt 0 ]]; then
    printf "WARNING: Found %s log entries matching filter '%s'.\n" "$COUNT" "$FILTER"
    printf "$(date -Iseconds) | boss_agent | LOG_SCAN | found %s entries\n" "$COUNT" >> ~/mycelial/logs/audit.log
    # Optionally, pass the log snippet to the Security Agent for further analysis
else
    printf "OK: No critical log entries found.\n"
    printf "$(date -Iseconds) | boss_agent | LOG_SCAN | clean\n" >> ~/mycelial/logs/audit.log
fi

# Cleanup
rm -f /tmp/mycelial_log_scan.txt
exit 0
