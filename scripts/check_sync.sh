#!/bin/bash
echo "🔍 Checking sync between .md and .py..."
for md in ~/mycelial/agents/*.md; do
    if [[ "$md" == *README.md ]]; then continue; fi
    agent=$(basename "$md" .md)
    py=~/mycelial/agents/${agent}.py
    if [[ ! -f "$py" ]]; then
        echo "❌ $agent.py not found"
        continue
    fi
    # Extract capabilities from YAML frontmatter
    caps=$(sed -n '/^---$/,/^---$/p' "$md" | grep -E '^  - ' | sed 's/^  - //')
    # Extract tasks from .py (look for 'elif task == "..."' or 'if task == "...":')
    tasks=$(grep -E '(elif|if) task == ' "$py" | sed -E "s/.*task == '([^']+)'.*/\1/" | sort | uniq)
    for cap in $caps; do
        if ! echo "$tasks" | grep -q "^$cap$"; then
            echo "⚠️ $agent.md has capability '$cap' but $agent.py does not."
        fi
    done
    for task in $tasks; do
        if ! echo "$caps" | grep -q "^$task$"; then
            echo "⚠️ $agent.py has task '$task' but $agent.md does not."
        fi
    done
done
