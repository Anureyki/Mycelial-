#!/home/anureyki/AgTechAI/venv/bin/python
import os, sys, json, argparse, random, subprocess
from datetime import datetime
import torch, torch.nn as nn, torch.optim as optim
import numpy as np
from collections import deque
BASE = os.path.expanduser("~/mycelial")
LOG = os.path.join(BASE, "logs", "audit.log")
def log(m): open(LOG,"a").write(f"{datetime.now().isoformat()} | ag | {m}\n")
class DQN(nn.Module):
    def __init__(self, s, a): super().__init__(); self.fc1=nn.Linear(s,64); self.fc2=nn.Linear(64,64); self.fc3=nn.Linear(64,a)
    def forward(self,x): return self.fc3(torch.relu(self.fc2(torch.relu(self.fc1(x)))))
class DQNAgent:
    def __init__(self, s=6, a=4, e=1.0):
        self.s, self.a, self.m, self.e, self.em, self.ed = s, a, deque(maxlen=2000), e, 0.01, 0.995
        self.model, self.target = DQN(s,a), DQN(s,a)
        self.opt = optim.Adam(self.model.parameters(), lr=0.001)
        self.crit = nn.MSELoss()
        self.path = os.path.join(BASE, "models", "dqn_model.pth")
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        if os.path.exists(self.path):
            try: self.model.load_state_dict(torch.load(self.path)); self.target.load_state_dict(torch.load(self.path)); log("✅ Loaded DQN")
            except: log("⚠️ New model")
    def remember(self,s,a,r,n,d): self.m.append((s,a,r,n,d))
    def act(self,s):
        if random.random() <= self.e: return random.randrange(self.a)
        with torch.no_grad(): return torch.argmax(self.model(torch.tensor(s,dtype=torch.float32).unsqueeze(0))).item()
    def replay(self, b=32):
        if len(self.m)<b: return
        for s,a,r,n,d in random.sample(self.m,b):
            t = self.model(torch.tensor(s,dtype=torch.float32).unsqueeze(0)).detach().numpy()[0]
            t[a] = r if d else r + 0.9 * torch.max(self.target(torch.tensor(n,dtype=torch.float32).unsqueeze(0))).item()
            self.opt.zero_grad(); loss = self.crit(self.model(torch.tensor(s,dtype=torch.float32).unsqueeze(0)), torch.tensor(t,dtype=torch.float32).unsqueeze(0)); loss.backward(); self.opt.step()
        if self.e > self.em: self.e *= self.ed
    def save(self): torch.save(self.model.state_dict(), self.path); log("💾 Saved DQN")
class AgAgent:
    def __init__(self): self.dqn = DQNAgent()
    def decide(self, data): a = self.dqn.act(data[:6]); log(f"Action: {['do_nothing','fan','heater','vent'][a]}"); return a
    def train(self, eps=100):
        log(f"🏋️ Training {eps} episodes...")
        for ep in range(eps):
            s, done, total = np.random.rand(6)*10, False, 0
            while not done:
                a = self.dqn.act(s); r = 1 if (s[0]>20 and s[1]<80) else -1; n = np.clip(s + np.random.randn(6)*0.1, 0, 10); done = random.random()<0.05
                self.dqn.remember(s,a,r,n,done); s=n; total+=r
                if done: self.dqn.replay()
            if ep%10==0: log(f"Ep {ep}: reward={total}, epsilon={self.dqn.e:.2f}")
        self.dqn.save(); log("✅ Training complete!")
def train(): AgAgent().train(100)
def decide():
    data = [23.5,65.0,0.8,1200,6.2,420]
    a = AgAgent().decide(data)
    print(f"Action: {a}")
p=argparse.ArgumentParser(); p.add_argument("--task", required=True); a=p.parse_args()
if a.task=="dqn_train": train()
elif a.task=="dqn_decide": decide()
else: print(f"❌ Unknown: {a.task}")
