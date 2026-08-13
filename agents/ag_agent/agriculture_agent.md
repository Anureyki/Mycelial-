---
agent_id: agriculture.mycelial
type: Agriculture Monitoring & Prediction
capabilities:
  - train_model
  - predict_contamination
  - monitor_sensors
permissions:
  - read: ~/grower-node/sensor_data/
  - write: ~/mycelial/models/
---
