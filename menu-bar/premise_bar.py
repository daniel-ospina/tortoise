#!/usr/bin/env python3
"""
premise-bar — Menu bar app launcher for internal services.

Shows registered services as green (running) or gray (stopped) icons.
Click to launch, stop, or restart. Reads service registry from JSON config.

Install:
  pip3 install --break-system-packages rumps
  python3 premise_bar.py

Config:
  ~/.config/premise/services.json
"""
import json
import os
import subprocess
import time
import socket
from pathlib import Path

import rumps

CONFIG_PATH = os.path.expanduser("~/.config/premise/services.json")
REFRESH_SECONDS = 10


def load_config():
    """Load service registry from JSON config."""
    path = Path(CONFIG_PATH)
    if not path.exists():
        # Create default config if missing
        path.parent.mkdir(parents=True, exist_ok=True)
        default = Path(__file__).parent.parent / "services" / "services.json"
        if default.exists():
            import shutil
            shutil.copy(default, path)
        else:
            path.write_text('{"services": []}')
    with open(path) as f:
        return json.load(f).get("services", [])


def check_http(url, timeout=2):
    """Check if HTTP service is responding."""
    try:
        import urllib.request
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except Exception:
        return False


def check_tcp(host, port, timeout=2):
    """Check if TCP port is open."""
    try:
        sock = socket.create_connection((host, port), timeout=timeout)
        sock.close()
        return True
    except Exception:
        return False


def check_process(name):
    """Check if a process is running by name."""
    try:
        result = subprocess.run(["pgrep", "-f", name], capture_output=True)
        return result.returncode == 0
    except Exception:
        return False


def check_launchctl(name):
    """Check if a LaunchAgent is loaded."""
    try:
        result = subprocess.run(
            ["launchctl", "list", name],
            capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False


def is_running(service):
    """Check if a service is currently running."""
    check = service.get("check", {})
    ctype = check.get("type", "")
    try:
        if ctype == "http":
            return check_http(check["url"])
        elif ctype == "tcp":
            return check_tcp(check["host"], check["port"])
        elif ctype == "process":
            return check_process(check["name"])
        elif ctype == "launchctl":
            return check_launchctl(check["name"])
        elif ctype == "pidfile":
            pid = int(Path(check["path"]).read_text().strip())
            os.kill(pid, 0)
            return True
    except Exception:
        pass
    return False


def launch_service(service):
    """Launch a service."""
    launch = service.get("launch", {})
    ltype = launch.get("type", "")
    try:
        if ltype == "docker-compose":
            d = os.path.expanduser(launch["dir"])
            subprocess.Popen(["docker", "compose", "up", "-d"], cwd=d,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif ltype == "shell":
            subprocess.Popen(launch["command"], shell=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        elif ltype == "open":
            subprocess.Popen(["open", launch["path"]],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as e:
        rumps.notification("Premise", f"Failed to launch {service['name']}", str(e))


def stop_service(service):
    """Stop a service."""
    stop = service.get("stop", {})
    stype = stop.get("type", "")
    try:
        if stype == "docker-compose-down":
            d = os.path.expanduser(stop["dir"])
            subprocess.run(["docker", "compose", "down"], cwd=d,
                           capture_output=True, timeout=30)
        elif stype == "shell":
            subprocess.run(stop["command"], shell=True,
                           capture_output=True, timeout=10)
    except Exception as e:
        rumps.notification("Premise", f"Failed to stop {service['name']}", str(e))



import os, sys, signal

# Dedup: only allow one instance
PID_FILE = os.path.expanduser("~/.premise-bar.pid")
if os.path.exists(PID_FILE):
    try:
        old_pid = int(open(PID_FILE).read().strip())
        os.kill(old_pid, signal.SIGTERM)
        import time; time.sleep(0.5)
    except (OSError, ValueError):
        pass
with open(PID_FILE, "w") as f:
    f.write(str(os.getpid()))


class PremiseBar(rumps.App):
    def __init__(self):
        super().__init__("⬡", title="⬡")
        self.services = []
        self.menu_items = {}
        self.timer = rumps.Timer(self.refresh, REFRESH_SECONDS)
        self.timer.start()
        self.refresh(None)

    def refresh(self, _):
        """Refresh service status and rebuild menu."""
        self.services = load_config()
        running_count = 0
        
        for svc in self.services:
            sid = svc["id"]
            running = is_running(svc)
            if running:
                running_count += 1
            
            label = f"⚫ {svc['icon']}  {svc['name']}"
            if running:
                label = f"🟢 {svc['icon']}  {svc['name']}"
            
            # Remove old menu item if exists
            if sid in self.menu_items:
                try:
                    del self.menu[self.menu_items[sid].title]
                except KeyError:
                    pass
            
            # Add menu item
            item = rumps.MenuItem(label, callback=self.make_callback(sid))
            self.menu_items[sid] = item
        
        # Rebuild menu
        self.menu.clear()
        
        # Status header
        all_running = running_count == len(self.services)
        status_text = f"All {len(self.services)} running" if all_running else f"{running_count}/{len(self.services)} running"
        self.menu.add(rumps.MenuItem(status_text, callback=None))
        self.menu.add(rumps.separator)
        
        # Service items
        for svc in self.services:
            sid = svc["id"]
            if sid in self.menu_items:
                self.menu.add(self.menu_items[sid])
        
        self.menu.add(rumps.separator)
        
        # Recording controls
        recording = self._check_recording()
        rec_label = "🔴 Stop Recording" if recording else "🎙️  Start Recording"
        self.menu.add(rumps.MenuItem(rec_label, callback=self.toggle_recording))
        
        self.menu.add(rumps.separator)
        self.menu.add(rumps.MenuItem("🔄  Refresh", callback=self.refresh))
        self.menu.add(rumps.MenuItem("⚙️  Edit Config", callback=self.edit_config))
        
        # Update title
        self.title = "⬡" if all_running else "⬡"
        if running_count < len(self.services):
            self.title = f"⬡ {running_count}/{len(self.services)}"

    def make_callback(self, sid):
        """Create a callback for a service menu item."""
        def callback(sender):
            svc = next((s for s in self.services if s["id"] == sid), None)
            if not svc:
                return
            
            running = is_running(svc)
            
            if running:
                # Show submenu with actions
                url = svc.get("url")
                if url:
                    response = rumps.alert(
                        f"{svc['icon']} {svc['name']} — Running",
                        svc.get("description", ""),
                        "Open", "Restart", "Stop"
                    )
                else:
                    response = rumps.alert(
                        f"{svc['icon']} {svc['name']} — Running",
                        svc.get("description", ""),
                        "Restart", "Stop"
                    )
                    if response == 1:
                        response = 2  # remap to Restart
                    elif response == 2:
                        response = 3  # remap to Stop
                if response == 1 and url:  # Open
                    subprocess.Popen(["open", url])
                elif response == 2:  # Restart
                    stop_service(svc)
                    time.sleep(1)
                    launch_service(svc)
                    rumps.notification("Premise", f"Restarted {svc['name']}", "")
                elif response == 3:  # Stop
                    stop_service(svc)
                    rumps.notification("Premise", f"Stopped {svc['name']}", "")
            else:
                # Launch
                response = rumps.alert(
                    f"{svc['icon']} {svc['name']} — Stopped",
                    svc.get("description", ""),
                    "Launch", "Cancel"
                )
                if response == 1:
                    launch_service(svc)
                    rumps.notification("Premise", f"Launching {svc['name']}...", "")
        
        return callback

    def edit_config(self, _):
        """Open config file in default editor."""
        path = os.path.expanduser(CONFIG_PATH)
        subprocess.Popen(["open", "-t", path])

    def _check_recording(self):
        """Check if currently recording via minutes."""
        try:
            r = subprocess.run(["pgrep", "-f", "minutes record"], capture_output=True)
            return r.returncode == 0
        except Exception:
            return False

    def toggle_recording(self, _):
        """Start or stop recording."""
        if self._check_recording():
            subprocess.run(["minutes", "stop"], capture_output=True, timeout=10)
            rumps.notification("Minutes", "Recording stopped", "")
        else:
            subprocess.Popen(["minutes", "record"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            rumps.notification("Minutes", "Recording started", "")


if __name__ == "__main__":
    PremiseBar().run()
