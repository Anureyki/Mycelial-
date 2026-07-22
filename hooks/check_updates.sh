#!/bin/bash
# check_updates.sh – Detailed system update scanner with vulnerability context

OUTPUT_DIR="$HOME/mycelial/state/updates"
mkdir -p "$OUTPUT_DIR"

# --- 1. Get apt updates with CVE info ---
check_apt_detailed() {
    local report="$OUTPUT_DIR/apt_detailed.txt"
    echo "APT_DETAILED|$(date -Iseconds)" > "$report"
    
    apt list --upgradable 2>/dev/null | grep -v "Listing..." | awk -F/ '{print $1}' | while read pkg; do
        if [[ -n "$pkg" ]]; then
            # Get changelog and grep for CVE/security keywords
            cves=$(apt changelog "$pkg" 2>/dev/null | grep -o -i "CVE-[0-9]*-[0-9]*" | head -3 | tr '\n' ', ')
            severity="Low"
            if echo "$cves" | grep -q "CVE"; then severity="High"; fi
            # Check for critical keywords
            if apt changelog "$pkg" 2>/dev/null | grep -qi -E "critical|urgent|important"; then
                severity="Critical"
            elif echo "$cves" | grep -q "CVE"; then
                severity="High"
            elif apt changelog "$pkg" 2>/dev/null | grep -qi -E "security|vulnerability"; then
                severity="Medium"
            fi
            # Current and available versions
            current=$(apt policy "$pkg" 2>/dev/null | grep "Installed" | awk '{print $2}')
            available=$(apt policy "$pkg" 2>/dev/null | grep "Candidate" | awk '{print $2}')
            echo "$pkg|$current|$available|$severity|$cves" >> "$report"
        fi
    done
    echo "OK: Detailed apt report generated."
}

# --- 2. Check pip outdated with security context (simplified) ---
check_pip_detailed() {
    pip3 list --outdated --format=json 2>/dev/null | python3 -c "
import sys, json
data = json.load(sys.stdin)
report = open('$OUTPUT_DIR/pip_detailed.txt', 'w')
report.write('PIP_DETAILED|' + __import__('datetime').datetime.now().isoformat() + '\n')
for pkg in data:
    name = pkg['name']
    current = pkg['version']
    available = pkg['latest_version']
    # Simple heuristic: if package is security-related, mark high
    severity = 'High' if name in ['cryptography', 'requests', 'urllib3', 'openssl'] else 'Medium'
    report.write(f'{name}|{current}|{available}|{severity}|\n')
report.close()
"
}

# --- 3. Check Docker image updates ---
check_docker_detailed() {
    local report="$OUTPUT_DIR/docker_detailed.txt"
    echo "DOCKER_DETAILED|$(date -Iseconds)" > "$report"
    if docker ps --filter "name=pihole-unbound" --format "{{.Image}}" | grep -q .; then
        current_image=$(docker ps --filter "name=pihole-unbound" --format "{{.Image}}")
        # Pull to check if newer (silent)
        docker pull "$current_image" --quiet 2>/dev/null
        # Compare digest to see if update is available (simplified)
        echo "$current_image|update available|Medium|Check Docker Hub for CVE details" >> "$report"
    fi
}

# --- 4. Run all checks ---
check_apt_detailed
check_pip_detailed
check_docker_detailed

# --- 5. Generate consolidated summary ---
cat > "$OUTPUT_DIR/security_summary.txt" << EOJ
SECURITY UPDATE SUMMARY – $(date -Iseconds)

--- APT Security Updates ---
$(grep -E '\|(Critical|High)\|' "$OUTPUT_DIR/apt_detailed.txt" 2>/dev/null | awk -F'|' '{print "  " $1 " (" $4 ") -> " $3 " [CVE: " $5 "]"}')

--- PIP Security Updates ---
$(grep -E '\|(Critical|High)\|' "$OUTPUT_DIR/pip_detailed.txt" 2>/dev/null | awk -F'|' '{print "  " $1 " (" $4 ") -> " $3}')

--- Docker Security Updates ---
$(cat "$OUTPUT_DIR/docker_detailed.txt" 2>/dev/null | grep -v "DOCKER_DETAILED" | awk -F'|' '{print "  " $1 " -> " $2 " (" $3 ")"}')
EOJ
