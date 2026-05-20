#!/usr/bin/env python3
import subprocess, time, os, urllib.request

# Kill all Chrome first
subprocess.run(['taskkill.exe', '/F', '/IM', 'chrome.exe'], capture_output=True, timeout=10)
time.sleep(3)

# Launch Chrome
cmd = [
    '/mnt/c/Program Files/Google/Chrome/Application/chrome.exe',
    '--remote-debugging-port=9222',
    '--user-data-dir=C:\\Users\\jadam\\.config\\google-chrome-cdp',
    '--no-first-run',
    '--no-default-browser-check',
]
print('Launching:', cmd, flush=True)
proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd='/mnt/c/Windows/System32')
print('PID:', proc.pid, flush=True)
time.sleep(8)

# Check if port is listening
result = subprocess.run(['netstat.exe', '-ano'], capture_output=True, text=True, timeout=10)
lines = [l for l in result.stdout.splitlines() if '9222' in l]
print('Port 9222:', lines, flush=True)

# Try CDP
try:
    resp = urllib.request.urlopen('http://127.0.0.1:9222/json/version', timeout=2)
    print('CDP OK:', resp.read().decode()[:200], flush=True)
except Exception as e:
    print('CDP FAIL:', e, flush=True)
