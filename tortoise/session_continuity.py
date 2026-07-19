"""Session continuity — vibecoder wedge prototype.
Auto-capture findings during a session, auto-retrieve on next session start.
Zero config. The agent IS the interface."""
from tortoise.sdk import TortoiseSDK
from datetime import datetime

class SessionContinuity:
    """Wraps a session with auto-capture and auto-retrieve."""
    
    def __init__(self, db_path="tortoise.db"):
        self.sdk = TortoiseSDK(db_path)
        self.session_id = None
        self.findings = []
    
    def start(self, topic="General"):
        """Called at session start. Returns context from prior sessions."""
        self.session_id = datetime.now().strftime("session-%Y%m%d-%H%M%S")
        
        # Find recent context
        recent = self.sdk.query(context=self.session_id) if False else []
        prior = self.sdk.query(kind="observation")
        
        summary = []
        # Get last 5 findings from any session
        if prior:
            summary = [p.get("content", "")[:120] for p in prior[-5:]]
        
        print(f"Session: {self.session_id}")
        print(f"Topic: {topic}")
        if summary:
            print("\nFrom your last session:")
            for s in summary:
                print(f"  - {s}...")
        else:
            print("\nNo prior context found.")
        print()
        return self.session_id
    
    def capture(self, content, kind="observation", **props):
        """Capture a finding during the session."""
        point = self.sdk.create_point(kind, content, 
            context=self.session_id,
            captured_at=datetime.now().isoformat(),
            **props)
        self.findings.append(point)
        return point
    
    def end(self):
        """Called at session end. Persists findings and runs confidence."""
        if self.findings:
            print(f"\nCaptured {len(self.findings)} findings from this session.")
            # Run confidence on new findings
            try:
                result = self.sdk.compute_confidence()
                print(f"Confidence updated: {result.get('iterations', '?')} iterations")
            except:
                pass
        
        # Report what was captured
        for f in self.findings:
            content = f.get("content", "")[:80]
            print(f"  [{f.get('pointKind', '?')}] {content}...")
        
        self.sdk.close()


# Demo script
if __name__ == "__main__":
    sc = SessionContinuity()
    session_id = sc.start("Researching React auth libraries")
    
    # Simulate findings during session
    sc.capture("Lucia Auth has been deprecated as of 2025", kind="observation")
    sc.capture("Better-Auth is the official successor to Lucia", kind="observation")
    sc.capture("Clerk is overkill for solo projects — use Better-Auth", kind="decision")
    
    sc.end()
