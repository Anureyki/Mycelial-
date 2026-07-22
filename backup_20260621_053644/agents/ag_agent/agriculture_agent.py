#!/usr/bin/env python3
"""Agriculture Agent - DQN for sensor control"""
import os
import sys
import json
import argparse
import subprocess
from datetime import datetime
import torch
import torch.nn as nn
import torch.optim as optim
import numpy as np
import random
from collections import deque

BASE = os.path.expanduser("~/mycelial")
LOG_FILE = os.path.join(BASE, "logs", "audit.log")

def log(msg):
    with open(LOG_FILE, "a") as f:
        f.write(f"{datetime.now().isoformat()} | ag_agent | {msg}\n")

class DQN(nn.Module):
    def __init__(self, state_size, action_size):
        super(DQN, self).__init__()
        self.fc1 = nn.Linear(state_size, 64)
        self.fc2 = nn.Linear(64, 64)
        self.fc3 = nn.Linear(64, action_size)
    
    def forward(self, x):
        x = torch.relu(self.fc1(x))
        x = torch.relu(self.fc2(x))
        return self.fc3(x)

class DQNAgent:
    def __init__(self, state_size=6, action_size=4, epsilon=1.0):
        self.state_size = state_size
        self.action_size = action_size
        self.memory = deque(maxlen=2000)
        self.epsilon = epsilon
        self.epsilon_min = 0.01
        self.epsilon_decay = 0.995
        self.model = DQN(state_size, action_size)
        self.target_model = DQN(state_size, action_size)
        self.optimizer = optim.Adam(self.model.parameters(), lr=0.001)
        self.criterion = nn.MSELoss()
        self.model_path = os.path.join(BASE, "models", "dqn_model.pth")
        os.makedirs(os.path.dirname(self.model_path), exist_ok=True)
        if os.path.exists(self.model_path):
            try:
                self.model.load_state_dict(torch.load(self.model_path))
                self.target_model.load_state_dict(torch.load(self.model_path))
                log("✅ Loaded existing DQN model")
            except:
                log("⚠️ Could not load model, starting fresh")
    
    def remember(self, state, action, reward, next_state, done):
        self.memory.append((state, action, reward, next_state, done))
    
    def act(self, state):
        if np.random.rand() <= self.epsilon:
            return random.randrange(self.action_size)
        with torch.no_grad():
            state_tensor = torch.tensor(state, dtype=torch.float32).unsqueeze(0)
            q_values = self.model(state_tensor)
            return torch.argmax(q_values).item()
    
    def replay(self, batch_size=32):
        if len(self.memory) < batch_size:
            return
        batch = random.sample(self.memory, batch_size)
        for state, action, reward, next_state, done in batch:
            state_t = torch.tensor(state, dtype=torch.float32).unsqueeze(0)
            next_state_t = torch.tensor(next_state, dtype=torch.float32).unsqueeze(0)
            target = self.model(state_t).detach().numpy()[0]
            if done:
                target[action] = reward
            else:
                target[action] = reward + 0.9 * torch.max(self.target_model(next_state_t)).item()
            target_t = torch.tensor(target, dtype=torch.float32).unsqueeze(0)
            self.optimizer.zero_grad()
            loss = self.criterion(self.model(state_t), target_t)
            loss.backward()
            self.optimizer.step()
        if self.epsilon > self.epsilon_min:
            self.epsilon *= self.epsilon_decay
    
    def save(self):
        torch.save(self.model.state_dict(), self.model_path)
        log(f"💾 DQN model saved to {self.model_path}")

class AgricultureAgent:
    def __init__(self):
        self.state_size = 6
        self.action_size = 4
        self.dqn = DQNAgent(self.state_size, self.action_size)
    
    def decide_action(self, sensor_data):
        state = np.array(sensor_data[:self.state_size])
        action = self.dqn.act(state)
        actions = ["do_nothing", "turn_on_fan", "turn_on_heater", "open_vent"]
        log(f"🧠 DQN decision: {actions[action]}")
        return action
    
    def train_with_synthetic_data(self, episodes=100):
        log(f"🏋️ Training DQN on {episodes} episodes...")
        for episode in range(episodes):
            state = np.random.rand(self.state_size) * 10
            done = False
            total_reward = 0
            while not done:
                action = self.dqn.act(state)
                reward = 1 if (state[0] > 20 and state[1] < 80) else -1
                next_state = state + np.random.randn(self.state_size) * 0.1
                next_state = np.clip(next_state, 0, 10)
                done = np.random.rand() < 0.05
                self.dqn.remember(state, action, reward, next_state, done)
                state = next_state
                total_reward += reward
                if done:
                    self.dqn.replay()
            if episode % 10 == 0:
                log(f"Episode {episode}: Total Reward = {total_reward}, Epsilon = {self.dqn.epsilon:.2f}")
        self.dqn.save()
        log("✅ DQN training complete!")

def dqn_train():
    agent = AgricultureAgent()
    agent.train_with_synthetic_data(100)

def dqn_decide():
    agent = AgricultureAgent()
    sensor_data = [23.5, 65.0, 0.8, 1200, 6.2, 420]
    action = agent.decide_action(sensor_data)
    print(f"Action: {action}")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    
    if args.task == "dqn_train":
        dqn_train()
    elif args.task == "dqn_decide":
        dqn_decide()
    else:
        print(f"❌ Unknown task: {args.task}")

if __name__ == "__main__":
    main()
