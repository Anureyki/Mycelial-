#!/usr/bin/env python3
"""
Provenance Service - Platform Service.
Records how artifacts are created, modified, reviewed, and orchestrated
across human and agent actors, and answers questions about that history
(lineage, origin classification, integrity verification).

This is the ONLY thing that writes to the provenance store - agents call
into it (directly, or via AgentBase.record_provenance_event once an agent
chooses to instrument a call site) rather than keeping their own
provenance bookkeeping. See core/provenance_schemas.py for the event
vocabulary and core/provenance_manager.py for the storage/derivation
logic this service wraps.

Scope note: this is the foundation layer only (event schema, storage,
lineage, contribution tracking, origin classification, verification).
The visual seal generator, Anansi presentation integration, and Git/GitHub
integration are deliberately not part of this service yet.
"""
import os
import sys
from flask import Flask, request, jsonify

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from core.provenance_manager import ProvenanceManager, ArtifactConflictError
from core.provenance_schemas import new_provenance_event

app = Flask(__name__)
manager = ProvenanceManager()


@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "healthy", "service": "provenance"})


@app.route("/execute", methods=["POST"])
def execute():
    data = request.json or {}
    task = data.get("task")
    args = data.get("args", {})
    if task is None and "params" in data:
        params = data["params"]
        task = params.get("task")
        args = params.get("args", {})
    if not isinstance(args, dict):
        return jsonify({"error": "args must be an object for the provenance service"}), 400

    try:
        if task == "record_event":
            event = new_provenance_event(
                operation=args.get("operation"),
                actor_type=args.get("actor_type"),
                actor_id=args.get("actor_id"),
                artifact_id=args.get("artifact_id"),
                parent_artifact_id=args.get("parent_artifact_id"),
                execution_id=args.get("execution_id"),
                agent_id=args.get("agent_id"),
                model_id=args.get("model_id"),
                tools_used=args.get("tools_used"),
                input_artifacts=args.get("input_artifacts"),
                output_artifacts=args.get("output_artifacts"),
                human_contribution=args.get("human_contribution"),
                metadata=args.get("metadata"),
            )
            recorded = manager.record_event(event, artifact_content=args.get("artifact_content"))
            return jsonify({"result": recorded})

        elif task == "get_artifact_history":
            artifact_id = args.get("artifact_id")
            if not artifact_id:
                return jsonify({"error": "Missing artifact_id"}), 400
            history = manager.get_artifact_history(artifact_id)
            if history is None:
                return jsonify({"error": f"No provenance record for artifact_id {artifact_id!r}"}), 404
            return jsonify({"result": history})

        elif task == "get_execution_events":
            execution_id = args.get("execution_id")
            if not execution_id:
                return jsonify({"error": "Missing execution_id"}), 400
            return jsonify({"result": {"events": manager.get_execution_events(execution_id)}})

        elif task == "get_lineage":
            artifact_id = args.get("artifact_id")
            if not artifact_id:
                return jsonify({"error": "Missing artifact_id"}), 400
            return jsonify({"result": manager.get_lineage(artifact_id)})

        elif task == "classify_origin":
            artifact_id = args.get("artifact_id")
            if not artifact_id:
                return jsonify({"error": "Missing artifact_id"}), 400
            return jsonify({"result": {"artifact_id": artifact_id,
                                        "origin_classification": manager.classify_artifact_origin(artifact_id)}})

        elif task == "verify_artifact":
            artifact_id = args.get("artifact_id")
            if not artifact_id:
                return jsonify({"error": "Missing artifact_id"}), 400
            return jsonify({"result": manager.verify_artifact(artifact_id, args.get("content"))})

        else:
            return jsonify({"error": f"Unknown task: {task}"}), 400

    except ArtifactConflictError as e:
        return jsonify({"error": str(e)}), 409
    except ValueError as e:
        return jsonify({"error": str(e)}), 400


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8016, debug=False)
