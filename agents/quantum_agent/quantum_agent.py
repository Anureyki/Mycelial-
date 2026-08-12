#!/usr/bin/env python3
import sys
import os
import time

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from core.base_agent import AgentBase

from qiskit import QuantumCircuit
from qiskit.providers.basic_provider import BasicSimulator

MAX_QUBITS = 20  # guard against a caller asking for an unreasonably large circuit
MAX_SHOTS = 8192

GATE_ARITY = {
    "h": 1, "x": 1, "y": 1, "z": 1, "s": 1, "t": 1,
    "cx": 2, "cz": 2, "swap": 2,
}


def _apply_gate(qc, name, qubits):
    name = name.lower()
    if name not in GATE_ARITY:
        raise ValueError(f"Unsupported gate: {name}")
    if len(qubits) != GATE_ARITY[name]:
        raise ValueError(f"Gate {name} expects {GATE_ARITY[name]} qubit(s), got {len(qubits)}")
    getattr(qc, name)(*qubits)


class QuantumAgent(AgentBase):
    def __init__(self):
        super().__init__(
            agent_id="quantum_agent",
            port=9014,
            capabilities=["run_circuit", "bell_state", "random_bits"],
            role="quantum"
        )
        self.simulator = BasicSimulator()
        self.log("Quantum Agent started (qiskit BasicSimulator, classical simulation).")

    def _run(self, qc, shots):
        shots = max(1, min(int(shots), MAX_SHOTS))
        result = self.simulator.run(qc, shots=shots).result()
        return result.get_counts(), shots

    def handle_task(self, task, args, sender):
        args = args or {}

        if task == "bell_state":
            shots = args.get("shots", 100)
            qc = QuantumCircuit(2, 2)
            qc.h(0)
            qc.cx(0, 1)
            qc.measure([0, 1], [0, 1])
            counts, shots = self._run(qc, shots)
            return {
                "circuit": "bell_state",
                "shots": shots,
                "counts": counts,
                "note": "Simulated on qiskit's BasicSimulator, not physical quantum hardware.",
            }

        elif task == "random_bits":
            num_bits = args.get("num_bits", 8)
            if not isinstance(num_bits, int) or num_bits < 1 or num_bits > MAX_QUBITS:
                return {"error": f"num_bits must be an integer between 1 and {MAX_QUBITS}"}
            qc = QuantumCircuit(num_bits, num_bits)
            for i in range(num_bits):
                qc.h(i)
            qc.measure(range(num_bits), range(num_bits))
            counts, _ = self._run(qc, shots=1)
            bitstring = next(iter(counts))
            return {
                "bits": bitstring,
                "num_bits": num_bits,
                "note": "Derived from measuring an equal superposition on qiskit's BasicSimulator (a classical PRNG under the hood, not physical hardware) - not a source of cryptographic randomness.",
            }

        elif task == "run_circuit":
            num_qubits = args.get("num_qubits")
            gates = args.get("gates")
            shots = args.get("shots", 100)
            if not isinstance(num_qubits, int) or num_qubits < 1 or num_qubits > MAX_QUBITS:
                return {"error": f"num_qubits must be an integer between 1 and {MAX_QUBITS}"}
            if not isinstance(gates, list):
                return {"error": "gates must be a list of [name, qubit, ...] entries, e.g. [['h', 0], ['cx', 0, 1]]"}
            qc = QuantumCircuit(num_qubits, num_qubits)
            try:
                for entry in gates:
                    if not isinstance(entry, list) or not entry:
                        raise ValueError(f"Malformed gate entry: {entry}")
                    _apply_gate(qc, entry[0], entry[1:])
            except ValueError as e:
                return {"error": str(e)}
            qc.measure(range(num_qubits), range(num_qubits))
            counts, shots = self._run(qc, shots)
            return {
                "circuit": "custom",
                "num_qubits": num_qubits,
                "gates": gates,
                "shots": shots,
                "counts": counts,
                "note": "Simulated on qiskit's BasicSimulator, not physical quantum hardware.",
            }

        else:
            return {"error": f"Unknown task: {task}"}


if __name__ == "__main__":
    agent = QuantumAgent()
    while True:
        time.sleep(60)
        agent.heartbeat()
