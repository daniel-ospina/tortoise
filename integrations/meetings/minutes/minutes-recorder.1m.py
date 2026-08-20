#!/usr/bin/env python3
# <xbar.title>Minutes Recorder</xbar.title>
# <xbar.version>v1.0</xbar.version>
# <xbar.author>El Dato</xbar.author>
# <xbar.desc>Menu bar widget for Minutes meeting recorder — start/stop with visual status</xbar.desc>
# <xbar.dependencies>python3,minutes</xbar.dependencies>
# <xbar.abouturl>https://useminutes.app</xbar.abouturl>
#
# Install: copy to ~/Library/Application Support/SwiftBar/plugins/
#          chmod +x minutes-recorder.1m.py

import subprocess  # noqa: F401, I001
import os
import json
from pathlib import Path

STATE_FILE = os.path.expanduser("~/.minutes/cal-trigger-state.json")
MEETINGS_DIR = os.path.expanduser("~/meetings")

def get_recording_status():
    """Check if currently recording by reading cal-trigger state."""
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                state = json.load(f)
            pid = state.get("recording_pid")
            if pid:
                try:
                    os.kill(pid, 0)
                    title = state.get("event_title", "Meeting")
                    started = state.get("started_at", "")
                    return True, title, started
                except OSError:
                    pass
        except (json.JSONDecodeError, IOError):  # noqa: UP024
            pass
    return False, None, None


def get_recent_meetings():
    """Get count of recent meeting files."""
    p = Path(MEETINGS_DIR)
    if p.exists():
        return len(list(p.glob("*.md")))
    return 0


def main():
    recording, title, started = get_recording_status()
    recent = get_recent_meetings()
    
    if recording:
        # Show red dot + meeting title
        print(f"🔴 REC | size=12")  # noqa: F541
        print("---")
        print(f"Recording: {title}")
        if started:
            print(f"Since: {started[:16]}")
        print("---")
        print("Stop Recording | bash=minutes param1=stop terminal=false")
    else:
        # Show idle state
        if recent > 0:
            print(f"🎙️ {recent} meetings | size=12")
        else:
            print("🎙️ | size=12")
        print("---")
        print(f"Recordings: {recent}")
        print("---")
        print("Start Recording | bash=minutes param1=record terminal=false")
    
    # Common menu items
    print("---")
    print(f"Open Meetings Folder | href=file://{MEETINGS_DIR}")
    print("Health Check | bash=minutes param1=health terminal=true refresh=true")


if __name__ == "__main__":
    main()
