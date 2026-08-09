"""Session continuity — vibecoder wedge prototype.
Auto-capture findings during a session, auto-retrieve on next session start.
Zero config. The agent IS the interface."""
from tortoise.sdk import TortoiseSDK
from datetime import datetime

class SessionContinuity:
    """Wraps a session with auto-capture and auto-retrieve."""
    
    def __init__(self, db_path: str | None = None):
        self.sdk = TortoiseSDK(db_path)
        self.session_id = None
        self.findings = []
    
    def start(self, topic="General"):
        """Called at session start. Returns context from prior sessions."""
        self.session_id = datetime.now().strftime("session-%Y%m%d-%H%M%S")
        
        # Find recent context (context-based query removed #49 Phase 2)
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
        # P1 #49: use session_id property instead of deprecated context
        point = self.sdk.create_point(kind, content, 
            session_id=self.session_id,
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
    # Demo — requires TORTOISE_DB_URI or pass db_path explicitly
    import os, sys
    from tortoise.config import resolve_db_path, is_db_uri
    _uri = os.environ.get("TORTOISE_DB_URI", "")
    if is_db_uri(_uri):
        # #715 P2 conf 75: a supported URI must route through the SDK's URI
        # mode (from_uri) — resolve_db_path() would silently fall back to the
        # embedded default while the URI points elsewhere (split graph).
        print("Using DB URI (TORTOISE_DB_URI set)")
        sc = SessionContinuity(db_path=None)
    else:
        db_path = resolve_db_path()  # canonical path; respects TORTOISE_DB_PATH
        print(f"Using embedded DB at {db_path} (set TORTOISE_DB_PATH or TORTOISE_DB_URI to override)")
        sc = SessionContinuity(db_path=db_path)
    session_id = sc.start("Researching React auth libraries")
    
    # Simulate findings during session
    sc.capture("Lucia Auth has been deprecated as of 2025", kind="observation")
    sc.capture("Better-Auth is the official successor to Lucia", kind="observation")
    sc.capture("Clerk is overkill for solo projects — use Better-Auth", kind="decision")
    
    sc.end()
