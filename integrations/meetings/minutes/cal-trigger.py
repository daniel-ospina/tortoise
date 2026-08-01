#!/usr/bin/env python3
"""
cal-trigger.py — Auto-start Minutes recording when a calendar event begins.

Polls macOS Calendar every 60 seconds via AppleScript. When an event starts
within the next minute, launches `minutes record`. Stops when event ends or
after max duration.

Install:
  cp cal-trigger.py ~/.minutes/
  cp get_upcoming_events.applescript ~/.minutes/
  cp com.minutes.cal-trigger.plist ~/Library/LaunchAgents/
  launchctl load ~/Library/LaunchAgents/com.minutes.cal-trigger.plist
"""
import subprocess
import time
import os
import json
import sys
from datetime import datetime, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

CHECK_INTERVAL = 60
PRE_START_BUFFER = 60
MAX_RECORDING_MINUTES = 120
STATE_FILE = os.path.expanduser("~/.minutes/cal-trigger-state.json")
LOG_FILE = os.path.expanduser("~/.minutes/cal-trigger.log")
APPLESCRIPT = os.path.join(SCRIPT_DIR, "get_upcoming_events.applescript")


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


def get_upcoming_events():
    """Query macOS Calendar via AppleScript."""
    try:
        r = subprocess.run(
            ["osascript", APPLESCRIPT],
            capture_output=True, text=True, timeout=15
        )
        if r.returncode != 0 or not r.stdout.strip():
            return []
        events = []
        for line in r.stdout.strip().split("\n"):
            line = line.strip()
            if not line:
                continue
            parts = line.split("|||")
            if len(parts) < 3:
                continue
            title, start_str, end_str = parts[0], parts[1], parts[2]
            uid = parts[3] if len(parts) > 3 else ""
            
            # Parse attendees from remaining parts
            attendees = []
            for p in parts[4:]:
                p = p.strip()
                if p.startswith("ATTENDEE:") or p.startswith("ORGANIZER:"):
                    # Format: ATTENDEE:Name <email>
                    content = p.split(":", 1)[1].strip()
                    if "<" in content and ">" in content:
                        name = content[:content.index("<")].strip()
                        email = content[content.index("<")+1:content.index(">")].strip()
                        attendees.append({"name": name, "email": email})
            
            try:
                for fmt in [
                    "%A, %B %d, %Y at %I:%M:%S %p",
                    "%A, %d %B %Y at %H:%M:%S",
                ]:
                    try:
                        start = datetime.strptime(start_str.strip(), fmt)
                        end = datetime.strptime(end_str.strip(), fmt)
                        break
                    except ValueError:
                        continue
                else:
                    continue
                events.append({
                    "title": title,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "uid": uid,
                    "attendees": attendees,
                })
            except Exception:
                continue
        return events
    except Exception:
        return []


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            pass
    return {"recording_pid": None, "current_event_uid": None, "started_at": None}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def is_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def start_recording(event):
    title = event.get("title", "Meeting")[:50]
    log(f"▶  Recording: {title}")
    
    # macOS notification
    subprocess.run([
        "osascript", "-e",
        f'display notification \"Auto-recording started\" with title \"🎙️ {title}\" sound name \"Pop\"'
    ], timeout=5)
    
    proc = subprocess.Popen(
        ["minutes", "record"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    time.sleep(2)
    if proc.poll() is not None:
        log(f"❌ minutes record exited ({proc.returncode})")
        return None
    state = {
        "recording_pid": proc.pid,
        "current_event_uid": event.get("uid"),
        "started_at": datetime.now().isoformat(),
        "event_title": title,
        "attendees": event.get("attendees", []),
    }
    save_state(state)
    return proc.pid


def stop_recording():
    state = load_state()
    pid = state.get("recording_pid")
    if pid and is_alive(pid):
        log(f"⏹  Stopping (PID {pid})")
        subprocess.run(["minutes", "stop"], capture_output=True, timeout=30)
        log("✅ Stopped")
    save_state({"recording_pid": None, "current_event_uid": None, "started_at": None})


def main():
    log("Calendar trigger started")
    while True:
        try:
            state = load_state()
            pid = state.get("recording_pid")
            uid = state.get("current_event_uid")
            started = state.get("started_at")

            if pid:
                if not is_alive(pid):
                    log("Process died")
                    stop_recording()
                    pid = None
                elif started:
                    elapsed = datetime.now() - datetime.fromisoformat(started)
                    if elapsed > timedelta(minutes=MAX_RECORDING_MINUTES):
                        log(f"Max time reached ({MAX_RECORDING_MINUTES}m)")
                        stop_recording()
                        pid = None

            events = get_upcoming_events()
            now = datetime.now()

            for ev in events:
                try:
                    start = datetime.fromisoformat(ev["start"])
                    end = datetime.fromisoformat(ev["end"])
                except (ValueError, KeyError):
                    continue

                if end < now:
                    continue

                until_start = (start - now).total_seconds()

                if until_start <= PRE_START_BUFFER and until_start >= -60:
                    if pid and uid != ev["uid"]:
                        stop_recording()
                        pid = None

                    if not pid:
                        log(f"📅 {ev['title']} (in {int(until_start)}s)")
                        pid = start_recording(ev)
                        if pid:
                            uid = ev["uid"]

                if pid and uid == ev["uid"] and end < now:
                    # Notify user meeting calendar-end reached — they stop manually
                    subprocess.run([
                        "osascript", "-e",
                        'display notification "Meeting ended on calendar" with title "Minutes" subtitle "Run: minutes stop"'
                    ], timeout=5)
                    log(f"Calendar end reached for: {ev['title']} — running, user stops manually")

        except Exception as e:
            log(f"Error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
