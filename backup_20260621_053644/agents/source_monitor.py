
def check_alerts():
    """Check if any sources have been unhealthy for > 1 hour"""
    state_dir = os.path.join(BASE, "state", "source_monitor")
    reports = [f for f in os.listdir(state_dir) if f.startswith("report_")]
    
    if not reports:
        return
    
    latest = sorted(reports)[-1]
    with open(os.path.join(state_dir, latest), 'r') as f:
        report = json.load(f)
    
    unreachable = [s for s in report["sources"] if s["status"] == "unreachable"]
    
    if unreachable:
        msg = f"⚠️ {len(unreachable)} sources are unreachable:\n"
        for s in unreachable:
            msg += f"  - {s['name']}: {s['url']}\n"
        
        # Log alert
        log(msg)
        
        # Notify Boss
        subprocess.run(
            f"~/mycelial/agents/boss_agent/boss_agent.py --task think --args 'Source alert: {msg[:200]}'",
            shell=True
        )
        
        print(msg)
        return {"alert": True, "sources": unreachable}
    else:
        print("✅ All sources healthy")
        return {"alert": False}

if __name__ == "__main__":
    if sys.argv[1] == "--task" and sys.argv[2] == "check_alerts":
        check_alerts()
    else:
        main()
