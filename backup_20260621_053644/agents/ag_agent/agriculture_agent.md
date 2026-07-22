---
agent_id: agriculture.mycelial
type: Agriculture Monitoring & Prediction
capabilities:
  - train_model
  - predict_contamination
  - monitor_sensors
hooks:
  pre: pre_train.sh
  post: post_train.sh
permissions:
  - read: ~/grower-node/sensor_data/
  - write: ~/AgTechAI/models/
  - execute: ~/AgTechAI/client.py
---
