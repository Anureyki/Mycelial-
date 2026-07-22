#!/bin/bash
# fl_train.sh – Run FL training with venv
cd ~/AgTechAI
source venv/bin/activate
python ~/mycelial/models/transformer/fl_client.py --mode "${1:-synth}" --type "${2:-cannabis}"
