#!/usr/bin/env python3
"""A3 Conversation Mining Pilot — GAP-15 gate check."""
import re, sys, json
from pathlib import Path

KEYWORDS = re.compile(r'\b(decision|agreed|let.s|plan|decide)\b', re.IGNORECASE)
MEM_DIR = Path.home() / '.mempalace'

files = sorted(MEM_DIR.glob('PENDING_FILE_*.md'))[:10]
if not files:
    print('FAIL: no transcript files found')
    sys.exit(1)

total_events = 0
for f in files:
    text = f.read_text(encoding='utf-8', errors='ignore')
    events = len(KEYWORDS.findall(text))
    total_events += events
    print(f'{f.name}: {events} events')

n = len(files)
ratio = total_events / n
print(f'\nEvents/session: {ratio:.1f} ({total_events} events / {n} files)')
print(f'Gate: {"PASS" if ratio >= 3 else "FAIL"} (need \u22653)')
sys.exit(0 if ratio >= 3 else 1)
