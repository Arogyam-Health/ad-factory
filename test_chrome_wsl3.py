#!/usr/bin/env python3
import subprocess, time, os, urllib.request

# Kill all Chrome first
subprocess.run(['taskkill.exe', '/F', '/IM', 'chrome.exe'], capture_output=True, timeout=10)
time.sleep(3)

# Get Windows host IP
ip_route = subprocess.run(['ip', 'route'], capture_output=True, text=True, timeout=5)
gw_line = [l for l in ip_route.stdout.splitlines() if 'default' in l]
win_host_ip = gw_line[0].split()[2] if gw_line else '127.0.0.1'
print('Windows host IP:', win_host_ip, flush=True)

# Launch Chrome via PowerShell Start-Process (runs as native Windows process)
ps_cmd = (
    'Start-Process "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
    '-ArgumentList @('
    "'--remote-debugging-port=9222',"
    "'--remote-debugging-address=0.0.0.0',"
    "'--user-data-dir=C:\\Users\\jadam\\.config\\google-chrome-cdp',"
    "'--no-first-run',"
    "'--no-default-browser-check'"
    ')'
)
print('Launching via PowerShell...', flush=True)
proc = subprocess.Popen(
    ['powershell.exe', '-Command', ps_cmd],
    stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    cwd='/mnt/c/Windows/System32',
)
proc.wait(timeout=10)
print('PowerShell exited', flush=True)
time.sleep(8)

# Check if port is listening
result = subprocess.run(['netstat.exe', '-ano'], capture_output=True, text=True, timeout=10)
lines = [l for l in result.stdout.splitlines() if '9222' in l]
print('Port 9222:', lines, flush=True)

# Try CDP via Windows host IP
cdp_url = f'http://{win_host_ip}:9222/json/version'
print('CDP URL:', cdp_url, flush=True)
try:
    resp = urllib.request.urlopen(cdp_url, timeout=5)
    print('CDP OK:', resp.read().decode()[:200], flush=True)
except Exception as e:
    print('CDP FAIL:', e, flush=True)

# Also try 127.0.0.1
try:
    resp = urllib.request.urlopen('http://127.0.0.1:9222/json/version', timeout=2)
    print('CDP via 127.0.0.1 OK:', resp.read().decode()[:100], flush=True)
except Exception as e:
    print('CDP via 127.0.0.1 FAIL:', e, flush=True)
