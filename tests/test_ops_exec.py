"""Tests for the general-ops read-only classifier + registry + /ops/exec.

The classifier IS the safety boundary (docs/plans/MAGICMIRROR_PLAN.md
"General ops agent"; hardened in sprint item M3, docs/sprint/M3-build.md).
The SSH key behind /ops/exec can do more than the policy allows, so a
regression here is a security hole, not a cosmetic bug. Every refusal
asserts its reason, not only the boolean."""
import json
import subprocess

import pytest
from fastapi.testclient import TestClient

import tool_service.ops as ops


# ── classifier: allowed ───────────────────────────────────────────────────────

# The pre-M3 allowed table, unchanged.
LEGACY_ALLOWED = [
    "uptime",
    "uptime -p",
    "/usr/bin/uptime",                         # absolute path in a bin dir
    "systemctl --user status magicmirror",
    "systemctl --user is-active magicmirror",
    "journalctl --user -u magicmirror -n 50",
    "git log --oneline -5",
    "git rev-parse --short HEAD",
    "df -h",
    "free -m",
    "ps aux",
    "ps aux | grep node",                      # pipe between read programs
    "cat ~/MagicMirror/config/config.js | grep module",
    "vcgencmd measure_temp",                   # Pi-specific, useful
    "npm ls --depth=0",
]

# Every distinct command the audit log (data/ops_audit.log, 87 entries to
# 2026-10-22) shows was allowed and run. All must stay allowed.
AUDIT_LOG_ALLOWED = [
    'cat /etc/os-release',
    'cat /etc/os-release | grep "PRETTY_NAME"',
    'cat /home/pi/MagicMirror/config/config.js',
    'cat /home/pi/MagicMirror/defaultmodules/updatenotification/git_helper.js',
    'cat /home/pi/MagicMirror/modules/default/weather/config.js',
    'df -h /home/pi /tmp',
    'du -sh /home/pi/MagicMirror',
    'git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror diff -- /home/pi/MagicMirror/package-lock.json',
    'git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror diff --stat',
    'git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror diff package-lock.json',
    'git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror log -3 --format=%h_%ci_%s',
    'git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror status',
    'git --git-dir=/home/pi/MagicMirror/.git --work-tree=/home/pi/MagicMirror status --short',
    'git --git-dir=/home/pi/MagicMirror/.git ls-files modules/default',
    'git --git-dir=/home/pi/MagicMirror/.git ls-files | grep -i updatenotification | head -8',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git --work-tree=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast log -3 --format=%h_%ad_%s --date=short',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git --work-tree=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast status',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git branch -r',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git remote -v',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP diff --stat',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP diff package-lock.json | head -25',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP log -1 --format=%h_%ad_%s --date=short',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP status',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git --work-tree=/home/pi/MagicMirror/modules/MMM-RAIN-MAP status --short',
    'git --git-dir=/home/pi/MagicMirror/modules/MMM-Sunpower-Monitor/.git --work-tree=/home/pi/MagicMirror/modules/MMM-Sunpower-Monitor status',
    'grep -n -i updatenotification -A6 /home/pi/MagicMirror/config/config.js',
    'grep -n httpHeaders /home/pi/MagicMirror/config/config.js',
    'grep -n install-mm /home/pi/MagicMirror/package.json',
    'grep -n module: /home/pi/MagicMirror/config/config.js',
    'grep -ri "out of date" /home/pi/MagicMirror/translations /home/pi/MagicMirror/modules/default/updatenotification',
    'grep -rn "UPDATE_NOTIFICATION" /home/pi/MagicMirror/translations/en.json',
    'grep -rn -i referrer /home/pi/MagicMirror/modules/MMM-RAIN-MAP/MMM-RAIN-MAP.js',
    'grep -rn -i referrerpolicy /home/pi/MagicMirror/index.html',
    'grep -rn httpHeaders /home/pi/MagicMirror/js/defaults.js',
    'hostname -I',
    'journalctl --since 12:47 --no-pager | grep -ci "update found"',
    'journalctl --user --since 12:25 --until 12:37 --no-pager -o short-iso',
    'journalctl --user -n3000 --no-pager | grep -iE updatenotification | tail -25',
    'journalctl --user -u magicmirror --since -6h --no-pager -o short-iso',
    'journalctl --user -u magicmirror --since 12:20 --until 12:40 --no-pager -o short-iso',
    'journalctl -n300 --no-pager | grep -i updatenotification | tail -12',
    'journalctl -n4000 --no-pager | grep -i "update found" | tail -10',
    'journalctl -n4000 --no-pager | grep -i updatenotification | tail -15',
    'ls -1t /home/pi/mm-backups',
    'ls -la /home/pi/mm-backups',
    'ls -lt --time-style=full-iso /home/pi/MagicMirror/.git/objects',
    'ls /home/pi/MagicMirror/modules',
    'ls /home/pi/MagicMirror/modules/default',
    'ls /home/pi/MagicMirror/node_modules/@magicmirror 2',
    'npm --version',
    'ps -eo lstart,args | grep -i electron | head -4',
    'ss -ltn',
    'ss -ltnp',
    'stat -c %y_%U_%n /home/pi/MagicMirror/package-lock.json /home/pi/MagicMirror/package.json',
    'stat -c %y_%n /home/pi/MagicMirror/.git/FETCH_HEAD',
    'stat -c %y_%n /home/pi/MagicMirror/.git/FETCH_HEAD /home/pi/MagicMirror/.git/index /home/pi/MagicMirror/.git/objects /home/pi/MagicMirror/.git/ORIG_HEAD',
    'stat -c %y_%n /home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/.git/FETCH_HEAD',
    'stat -c %y_%n /home/pi/MagicMirror/modules/MMM-RAIN-MAP/.git/FETCH_HEAD',
    'stat -c %y_%n /home/pi/MagicMirror/modules/MMM-Sunpower-Monitor/.git/FETCH_HEAD',
    'tail -n 80 /home/pi/MagicMirror/defaultmodules/updatenotification/git_helper.js',
    'uptime',
    'uptime -p',
]

# Forms the old classifier refused wrongly (roadmap quirks), bounded log
# reads, and the plan's other must-keep-working forms.
NEWLY_ALLOWED = [
    # git -C (was refused: the -C value was taken as the subcommand)
    "git -C /home/pi/MagicMirror status --short",
    "git -C /home/pi/MagicMirror diff --stat",
    "git -C /home/pi/MagicMirror log -3 --format=%h%x20%ci%x20%s",
    "git -C /home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast status",
    "git --git-dir /home/pi/MagicMirror/.git log -1",
    "git --no-pager -C /home/pi/MagicMirror show --stat HEAD",
    # quoted pipe characters are arguments, not pipes
    'grep -nE "address|port:|ipWhitelist|useHttps" /home/pi/MagicMirror/config/config.js',
    'grep -rni "out.of.date\\|outdated" /home/pi/MagicMirror/modules/MMM-OpenWeatherMapForecast/node_helper.js',
    'journalctl --user -u magicmirror --since today --no-pager | grep -iE "updatenotif|update.avail|behind" | tail -20',
    "grep -E 'a|b' /var/log/syslog",
    # a forbidden word inside a quoted argument is just text
    'grep "rm -rf" /var/log/syslog',
    "grep 'sudo reboot; rm -rf /' /var/log/syslog",
    'grep ">" /var/log/syslog',
    "echo 'a && b'",
    # bounded log reads
    "tail -n 50 /var/log/syslog",
    "tail -n50 /var/log/syslog",
    "journalctl -n 100 --no-pager",
    "pm2 logs --nostream --lines 50",
    "pm2 logs magicmirror --nostream",
    "pm2 list",
    # program rules: the read-only forms of the programs that got rules
    "sort -u /var/log/syslog",
    "sort -t : -k 3 /etc/passwd",
    "uniq -c /var/log/syslog",
    "uniq",
    "date",
    "date -u +%Y-%m-%dT%H:%M:%S",
    "date -d @1700000000 +%F",
    "date -Iseconds",
    "hostname",
    "hostname -I",
    "hostnamectl",
    "hostnamectl status",
    "ip addr",
    "ip a",
    "ip -4 addr show dev eth0",
    "ip -br link list",
    "ip route show table all",
    "ip neigh",
    "file /usr/bin/node",
    "file -mC /usr/bin/node",                    # -m takes "C" as its value; not -C
    "ss -tlnp",
    "free -h",
    "sensors",
    "vmstat",
    "ps -o pid,etime,args -C electron",
    "ps -ef",
    "node --version",
    "git branch -r",
    "git branch -a -vv",
    "git branch --list 'feat/x'",
    "git branch --show-current",
    "git tag",
    "git tag -l 'v2.*'",
    "git tag -n5",
    "git remote -v",
    "git remote show -n origin",
    "git remote get-url origin",
    "git show HEAD:config/config.js.sample",
    "git log --output-indicator-new=+ -p -1",
    "systemctl --user list-units --no-pager",
    "systemctl status magicmirror --no-pager -n 20",
    "systemctl",
    "npm --version",
    "npm view magicmirror version",
    "vcgencmd measure_clock arm",
    "vcgencmd get_throttled",
    # recursive grep below the home directory
    "grep -r updatenotification /home/pi/MagicMirror/modules",
    "grep -rn x ~/MagicMirror/js",
    "grep -r -e x MagicMirror/js",
    "zgrep -i error /var/log/syslog.2.gz",
    # paths: things that look like secrets but are not on the list
    "cat /etc/passwd",
    "ls -la /home/pi",
    "cat /home/pi/MagicMirror/config/config.js",
    "cat /usr/bin/../../etc/os-release",
    "cat a'b'\"c\"",
    "cat \\;",                                  # an escaped ';' is a file name
]


@pytest.mark.parametrize("cmd", LEGACY_ALLOWED + AUDIT_LOG_ALLOWED + NEWLY_ALLOWED)
def test_readonly_allowed(cmd):
    ok, reason = ops.classify_readonly(cmd)
    assert ok, f"{cmd!r} should be allowed, got: {reason}"
    assert reason == "read-only"


def test_audit_log_forms_are_pinned():
    # 62 distinct allowed forms in the 87-entry log; guards against the
    # table being trimmed to make the suite pass.
    assert len(set(AUDIT_LOG_ALLOWED)) == 62


# ── classifier: refused ───────────────────────────────────────────────────────

SHELL = "not allowed (read-only mode"   # tail of every shell-syntax refusal
DENY = "secrets deny list"

REFUSED = [
    # A1. The plan's table, verbatim, each with its specific reason
    ("env rm -rf /home/pi/MagicMirror",        "'env' is not on the read-only allowlist: it runs any program"),
    ('env sh -c "id"',                         "'env' is not on the read-only allowlist: it runs any program"),
    ("sort -o /home/pi/.bashrc /tmp/x",        "sort: -o writes a file"),
    ("uniq /etc/hostname /home/pi/out",        "uniq: at most one file operand"),
    ("git log --output=/home/pi/x",            "git log: --output writes a file"),
    ("ip link set eth0 down",                  "ip link: only a bare object or 'show'/'list'"),
    ("cat /home/pi/.ssh/id_rsa",               "matches the secrets deny list ('.ssh')"),
    ("printenv",                               "'printenv' is not on the read-only allowlist: it prints the environment"),
    ("printenv HA_TOKEN",                      "'printenv' is not on the read-only allowlist"),
    ("/usr/bin/env uptime",                    "'env' is not on the read-only allowlist"),
    ("tail -f /var/log/syslog",                "tail: -f follows forever"),
    ("pm2 logs",                               "'pm2 logs' streams forever; add --nostream"),

    # A2. Per-program rules
    ("sort --output=/tmp/x /etc/hostname",     "sort: --output writes a file"),
    ("sort --compress-program=sh /etc/hostname", "sort: --compress-program runs a program"),
    ("sort --files0-from=/tmp/list",           "sort: --files0-from reads the list"),
    ("git show --output=/tmp/x HEAD",          "git show: --output writes a file"),
    ("git diff --ext-diff",                    "git diff: --ext-diff runs an external diff"),
    ("git diff --no-index /etc/hostname /home/pi/x", "git diff: --no-index compares arbitrary paths"),
    ("date -s 2020-01-01",                     "date: -s sets the clock"),
    ("date --set=2020-01-01",                  "date: --set sets the clock"),
    ("date 010100002030",                      "date: operand '010100002030' sets the clock"),
    ("date -u 0101",                           "date: operand '0101' sets the clock"),
    ("tail --follow=name /var/log/syslog",     "tail: --follow follows forever"),
    ("tail -F /var/log/syslog",                "tail: -F follows forever"),
    ("tail --retry /var/log/syslog",           "tail: --retry follows forever"),
    ("tail -5f /var/log/syslog",               "tail: -f follows forever"),       # obsolete -NUMf
    ("tail +5f /var/log/syslog",               "tail: '+5f' follows forever"),
    ("journalctl -f",                          "journalctl: -f follows forever"),
    ("journalctl --follow -u magicmirror",     "journalctl: --follow follows forever"),
    ("journalctl --vacuum-size=1M",            "journalctl: --vacuum-size deletes journal files"),
    ("journalctl --vacuum-time=1d",            "journalctl: --vacuum-time deletes journal files"),
    ("journalctl --rotate",                    "journalctl: --rotate rotates"),
    ("journalctl --flush",                     "journalctl: --flush moves"),
    ("journalctl --cursor-file=/tmp/c",        "journalctl: --cursor-file writes a cursor file"),
    ("file -C -m /tmp/magic",                  "file: -C writes a compiled magic file"),
    ("file --compile -m /tmp/magic",           "file: --compile writes"),
    ("uniq -c /etc/hostname /home/pi/out",     "uniq: at most one file operand"),
    ("uniq - /home/pi/out",                    "uniq: at most one file operand"),   # '-' is stdin, still an operand
    ("hostname evil",                          "hostname: an operand sets the hostname"),
    ("hostname -F /etc/hostname",              "hostname: -F sets the hostname from a file"),
    ("hostname -b",                            "hostname: -b sets the hostname"),
    ("hostnamectl set-hostname evil",          "hostnamectl: only bare 'hostnamectl' or 'hostnamectl status'"),
    ("hostnamectl hostname evil",              "hostnamectl: only bare"),
    ("hostnamectl -H pi@x status",             "hostnamectl: -H targets another host"),
    ("ip link set dev eth0 down",              "ip link: only a bare object or 'show'/'list'"),
    ("ip link s eth0 down",                    "ip link: only a bare object or 'show'/'list' is allowed, got 's'"),   # 's' abbreviates 'set'
    ("ip addr add 10.0.0.9/24 dev eth0",       "ip addr: only a bare object"),
    ("ip route flush all",                     "ip route: only a bare object"),
    ("ip -batch /tmp/cmds",                    "ip: '-batch' is not an allowed option or object"),
    ("ip -force link show",                    "ip: '-force' is not an allowed option or object"),
    ("ip netns exec x sh",                     "ip: 'netns' is not an allowed option or object"),
    ("ip",                                     "ip: name an object"),
    ("pm2 logs --lines 50",                    "'pm2 logs' streams forever"),
    ("pm2 logs -- --nostream",                 "'pm2 logs' streams forever"),       # after '--' it is not a flag
    ("pm2 jlist",                              "pm2 'jlist' is not a read-only subcommand: it prints every process's environment"),
    ("pm2 restart all",                        "pm2 'restart' is not a read-only subcommand"),
    ("pm2 -s restart all",                     "pm2: put the subcommand first"),
    ("ss -K dst 10.0.0.1",                     "ss: -K kills sockets"),
    ("ss -tK",                                 "ss: -K kills sockets"),
    ("ss --kill",                              "ss: --kill kills sockets"),
    ("ss -D /tmp/dump",                        "ss: -D writes a dump file"),
    ("free -s 1",                              "free: -s repeats until stopped"),
    ("vmstat 1",                               "vmstat: an interval operand repeats"),
    ("iostat 2",                               "iostat: an interval operand repeats"),
    ("sensors -s",                             "sensors: -s writes limits"),
    ("ps auxe",                                "ps: BSD option 'auxe' includes 'e'"),
    ("ps eww",                                 "ps: BSD option 'eww' includes 'e'"),
    ("vcgencmd display_power 0",               "vcgencmd: allowed commands are"),
    ("vcgencmd measure_temp -x",               "vcgencmd: one command and at most one argument"),
    ("node -e 'require(\"fs\")'",              "node: only 'node --version'"),
    ("node /tmp/x.js",                         "node: only 'node --version'"),
    ("systemctl --user restart magicmirror",   "systemctl 'restart' is not a read-only subcommand"),
    ("systemctl --user mask magicmirror",      "systemctl 'mask' is not a read-only subcommand"),
    ("systemctl -p status restart magicmirror", "systemctl: put options after the subcommand"),   # value posing as subcommand
    ("systemctl --user show-environment",      "systemctl 'show-environment' is not a read-only subcommand: it prints the manager's environment"),
    ("systemctl status -H pi@x magicmirror",   "systemctl: -H targets another host"),
    ("systemctl status --host=pi@x",           "systemctl: --host targets another host"),
    ("npm install",                            "npm 'install' is not a read-only subcommand"),
    ("npm --prefix ls install",                "npm: put the subcommand first"),
    ("npm -g install x",                       "npm: put the subcommand first"),
    ("docker ps",                              "'docker' is not on the read-only allowlist: never used"),
    ("git pull",                               "git 'pull' is not a read-only subcommand"),
    ("git reset --hard",                       "git 'reset' is not a read-only subcommand"),
    ("git -c core.pager=sh log",               "git: option '-c' before the subcommand is not allowed"),
    ("git -c alias.st=!sh st",                 "git: option '-c' before the subcommand"),
    ("git --config-env=core.pager=X log",      "git: option '--config-env=core.pager=X' before the subcommand"),
    ("git --exec-path=/tmp log",               "git: option '--exec-path=/tmp' before the subcommand"),
    ("git -p log",                             "git: option '-p' before the subcommand"),
    ("git -C",                                 "git: -C needs a value"),
    ("git",                                    "git: name a read-only subcommand"),
    ("git branch evil",                        "git branch: an operand without --list creates a branch"),
    ("git branch -d main",                     "git branch: only listing options are allowed, got '-d'"),
    ("git branch -D main",                     "git branch: only listing options are allowed, got '-D'"),
    ("git branch -m a b",                      "git branch: only listing options are allowed, got '-m'"),
    ("git branch --set-upstream-to=origin/x",  "git branch: only listing options"),
    ("git branch -rd origin/x",                "git branch: only listing options are allowed, got '-rd'"),
    ("git tag v9",                             "git tag: an operand without --list creates a tag"),
    ("git tag -d v1",                          "git tag: only listing options"),
    ("git tag -a v9 -m x",                     "git tag: only listing options"),
    ("git remote add evil https://x",          "git remote 'add' is not read-only"),
    ("git remote set-url origin https://x",    "git remote 'set-url' is not read-only"),
    ("git remote prune origin",                "git remote 'prune' is not read-only"),
    ("git remote -v --foo",                    "git remote: option '--foo' is not allowed"),

    # A3. Other spellings of the same flag
    ("sort -uo /home/pi/out /etc/hostname",    "sort: -o writes a file (given as '-uo')"),
    ("sort -o/home/pi/out /etc/hostname",      "sort: -o writes a file"),
    ("sort /etc/hostname -o /home/pi/out",     "sort: -o writes a file"),          # flag after the operand
    ("sort --output /home/pi/out /etc/hostname", "sort: --output writes a file"),
    ("sort --out=/home/pi/out /etc/hostname",  "sort: --output writes a file (given as '--out=/home/pi/out')"),
    ("sort --o=/home/pi/out /etc/hostname",    "sort: --output writes a file"),
    ("sort --compress=sh /etc/hostname",       "sort: --compress-program runs a program"),
    ("sort /etc/hostname -- -o /home/pi/out",  "sort: -o writes a file"),          # refused after '--' too
    ("git log --out=/home/pi/x",               "git log: --output writes a file (given as '--out=/home/pi/x')"),
    ("git log --outp /home/pi/x",              "git log: --output writes a file"),
    ("git -C /home/pi/MagicMirror log --output /tmp/x", "git log: --output writes a file"),
    ("tail -fn 5 /var/log/syslog",             "tail: -f follows forever and never returns (burns the 30 s timeout); use -n N (given as '-fn')"),
    ("tail -n 5 -f /var/log/syslog",           "tail: -f follows forever"),
    ("tail --fol /var/log/syslog",             "tail: --follow follows forever"),
    ("journalctl -fu magicmirror",             "use -n N (given as '-fu')"),
    ("journalctl -n 5 -f",                     "journalctl: -f follows forever"),
    ("journalctl --vac=1M",                    "journalctl: --vacuum-"),
    ("date -us 2020",                          "date: -s sets the clock (given as '-us')"),
    ("date --se 2020",                         "date: --set sets the clock"),
    ("date -- 0101",                           "date: operand '0101' sets the clock"),
    ("file -bC -m /tmp/magic",                 "file: -C writes a compiled magic file (given as '-bC')"),
    ("hostname -- evil",                       "hostname: an operand sets the hostname"),
    ("uniq -- -c /home/pi/out",                "uniq: at most one file operand"),
    ("uniq --skip-fields 1 /etc/hostname /home/pi/out", "uniq: at most one file operand"),
    ("ss --ki",                                "ss: --kill kills sockets"),

    # A4. The path deny list, any program, any position, any spelling
    ("cat .ssh/id_rsa",                        f"{DENY} ('.ssh')"),
    ("cat ~/.ssh/id_rsa",                      f"{DENY} ('.ssh')"),
    ("cat /home/pi//.ssh//id_rsa",             f"{DENY} ('.ssh')"),
    ("cat /home/pi/MagicMirror/../.ssh/id_rsa", f"{DENY} ('.ssh')"),
    ("cat /home/pi/'.ssh'/id_rsa",             f"{DENY} ('.ssh')"),
    ("cat /home/pi/.s\\sh/id_rsa",             f"{DENY} ('.ssh')"),
    ('cat "/home/pi/.SSH/ID_RSA"',             f"{DENY} ('.ssh')"),
    ("cat id_ed25519",                         f"{DENY} ('id_*')"),
    ("head -c 100 /etc/shadow",                f"{DENY} ('shadow*')"),
    ("tail -n 5 /etc/gshadow",                 f"{DENY} ('gshadow*')"),
    ("cut -c1- /etc/shadow-",                  f"{DENY} ('shadow*')"),
    ("cat /home/pi/MagicMirror/.env",          f"{DENY} ('.env')"),
    ("cat .env.local",                         f"{DENY} ('.env.*')"),
    ("cat prod.env",                           f"{DENY} ('*.env')"),
    ("cat /etc/ssl/private/server.key",        f"{DENY} ('*.key')"),
    ("cat /etc/ssh/ssh_host_ed25519_key",      f"{DENY} ('*_key')"),
    ("cat cert.pem",                           f"{DENY} ('*.pem')"),
    ("cat /home/pi/hass_token.txt",            f"{DENY} ('*token*')"),
    ("cat /home/pi/.netrc",                    f"{DENY} ('.netrc')"),
    ("cat ~/.git-credentials",                 f"{DENY} ('.git-credentials')"),
    ("cat /proc/self/environ",                 f"{DENY} ('environ')"),
    ("cat /proc/1234/environ",                 f"{DENY} ('environ')"),
    ("grep -f /home/pi/.ssh/id_rsa x",         f"grep: argument '/home/pi/.ssh/id_rsa' matches the {DENY}"),
    ("grep -f~/.ssh/id_rsa x",                 f"{DENY} ('.ssh')"),             # attached short value
    ("grep -fid_rsa x",                        f"{DENY} ('id_*')"),
    ("grep --file=/home/pi/.ssh/id_rsa x",     f"{DENY} ('.ssh')"),
    ("wc -c /home/pi/.ssh/id_ed25519",         f"wc: argument"),
    ("sort /etc/shadow",                       f"sort: argument '/etc/shadow' matches the {DENY}"),
    ("uniq /etc/shadow",                       f"{DENY}"),
    ("less /home/pi/.ssh/id_rsa",              "'less' is not on the read-only allowlist"),
    ("file /home/pi/.ssh/id_rsa",              f"file: argument"),
    ("stat ~/.ssh/id_rsa",                     f"stat: argument"),
    ("ls ~/.ssh",                              f"ls: argument '~/.ssh' matches"),
    ("ls -la /home/pi/.ssh/",                  f"{DENY}"),
    ("git show HEAD:.env",                     f"{DENY} ('.env')"),
    ("git -C /home/pi/.ssh log",               f"{DENY} ('.ssh')"),
    ("git blame --contents /home/pi/.ssh/id_rsa x", f"{DENY}"),
    ("cat /etc/os-release | grep x /home/pi/.ssh/id_rsa", f"{DENY}"),   # later segment
    ("journalctl --file=/home/pi/.ssh/x",      f"{DENY}"),
    # globs and expansions that could reach a denied path
    ("cat ~/.ss?/id_rsa",                      f"unquoted '?' (glob)"),
    ("cat ~/.ssh/*",                           f"unquoted '*' (glob)"),
    ("cat /home/pi/[.]ssh/id_rsa",             f"unquoted '[' (glob)"),
    ("cat /home/pi/.{s,}sh/id_rsa",            f"unquoted '{{' (brace expansion)"),
    ("cat $HOME/.ssh/id_rsa",                  f"'$' (variable expansion)"),
    ("cat ${HOME}/x",                          f"'$' (variable expansion)"),
    ("cat \"$HOME/x\"",                        f"'$' (variable expansion)"),
    ("cat $'\\x2essh'/id_rsa",                 f"'$' (variable expansion)"),
    # recursive reads that can reach a secret without naming it
    ("grep -r BEGIN /home/pi",                 "grep: recursive search root '/home/pi' is too broad"),
    ("grep -r BEGIN ~",                        "grep: recursive search root '~' is too broad"),
    ("grep -r BEGIN ~/",                       "too broad"),
    ("grep -r BEGIN ~pi",                      "too broad"),
    ("grep -r BEGIN .",                        "grep: recursive search root '.' is too broad"),
    ("grep -r BEGIN",                          "grep: recursive search with no directory"),
    ("grep -rl x /",                           "too broad"),
    ("grep -rl x //home//pi/",                 "too broad"),
    ("grep -r x /home/pi/MagicMirror/..",      "contains '..'"),
    ("grep -r x /home/pi/MagicMirror/../..",   "contains '..'"),
    ("grep -r x /proc/1234/task",              "recursive search under /proc"),
    ("grep -r x /sys/class/net",               "recursive search under /sys"),
    ("grep -re x /home/pi",                    "too broad"),
    ("grep -r -e x -e y /home/pi/MagicMirror /etc", "too broad"),
    ("grep --recursive x /home",               "too broad"),
    ("grep --rec x /home",                     "too broad"),
    ("grep -R x /home/pi/MagicMirror",         "grep: -R follows every symlink"),
    ("grep --dereference-recursive x /home/pi/MagicMirror", "grep: --dereference-recursive follows"),
    ("grep -d recurse x /home",                "grep: -d sets the directory action"),
    ("grep --directories=recurse x /",         "grep: --directories sets"),
    ("egrep -r x /home/pi",                    "egrep: recursive search root"),

    # A5. Shell features that defeat the classifier
    ("echo $(reboot)",                         "'$(' (command substitution)"),
    ('echo "$(reboot)"',                       "'$(' (command substitution)"),
    ("cat `whoami`",                           "'`' (command substitution)"),
    ('echo "`reboot`"',                        "'`' (command substitution)"),
    ("cat <(ls)",                              "'<(' (process substitution)"),
    ("tee >(sh)",                              "'>(' (process substitution)"),
    ("cat /etc/passwd > /tmp/x",               "'>' (redirection)"),
    ("cat /etc/passwd >> /tmp/x",              "'>>' (redirection)"),
    ("cat < /home/pi/.ssh/id_rsa",             "'<' (redirection)"),
    ("cat <<EOF",                              "'<<' (here-document)"),
    ("cat <<< x",                              "'<<' (here-document)"),
    ("ls 2>/dev/null",                         "'>' (redirection)"),
    ("ls 2>&1",                                "'>' (redirection)"),
    ("uptime; rm -rf /",                       "';' (command chaining)"),
    ("uptime;rm -rf /",                        "';' (command chaining)"),
    ("uptime && reboot",                       "'&&' (command chaining)"),
    ("uptime || reboot",                       "'||' (command chaining)"),
    ("uptime & ",                              "'&' (background job)"),
    ("uptime & reboot",                        "'&' (background job)"),
    ("uptime |& cat",                          "'|&'"),
    ("uptime\nreboot",                         "newline or control character"),
    ("uptime\rreboot",                         "newline or control character"),
    ("uptime\x00reboot",                       "newline or control character"),
    ("(reboot)",                               "'(' (subshell)"),
    ("{ reboot; }",                            "unquoted '{' (brace expansion)"),
    ("uptime # comment",                       "unquoted '#' (comment)"),
    ("FOO=bar uptime",                         "env-assignment prefix 'FOO=bar'"),
    ("A=1 B=2 ls",                             "env-assignment prefix 'A=1'"),
    ("PATH=/tmp ls",                           "env-assignment prefix"),
    # pipes: every segment must be allowed
    ("ps aux | rm -rf /",                      "'rm' is not on the read-only allowlist"),
    ("cat /etc/hostname | sh",                 "'sh' is not on the read-only allowlist"),
    ("cat /etc/hostname | xargs rm",           "'xargs' is not on the read-only allowlist"),
    ("cat /etc/hostname | tail -f",            "tail: -f follows forever"),
    ("ps aux | uniq - /home/pi/out",           "uniq: at most one file operand"),
    ("grep -nE address|port /x",               "'port' is not on the read-only allowlist"),   # a real pipe
    ("ls |",                                   "empty pipeline segment"),
    ("| ls",                                   "empty pipeline segment"),
    ("ls | | ls",                              "empty pipeline segment"),

    # A6. Quoting does not hide a real program or flag
    ('sh -c "rm x"',                           "'sh' is not on the read-only allowlist"),
    ("bash -c 'id'",                           "'bash' is not on the read-only allowlist"),
    ('"rm" -rf /tmp/x',                        "'rm' is not on the read-only allowlist"),
    ("'sudo' reboot",                          "sudo is not allowed"),
    ("\\sudo reboot",                          "sudo is not allowed"),
    ("sort '-o' /home/pi/out /etc/hostname",   "sort: -o writes a file"),
    ('tail "-f" /var/log/syslog',              "tail: -f follows forever"),
    ("ls | 'sh'",                              "'sh' is not on the read-only allowlist"),

    # A7. Refusal is the default
    ("reboot",                                 "'reboot' is not on the read-only allowlist"),
    ("sudo reboot",                            "sudo is not allowed"),
    ("/usr/bin/sudo reboot",                   "sudo is not allowed"),
    ("rm -rf ~/MagicMirror",                   "'rm' is not on the read-only allowlist"),
    ("sed -i s/a/b/ config.js",                "'sed' is not on the read-only allowlist"),
    ("find / -name x",                         "'find' is not on the read-only allowlist"),
    ("awk '{print}' /etc/hostname",            "'awk' is not on the read-only allowlist"),
    ("tee /tmp/x",                             "'tee' is not on the read-only allowlist"),
    ("kill 1",                                 "'kill' is not on the read-only allowlist"),
    ("cd /tmp",                                "'cd' is not on the read-only allowlist"),
    ("/tmp/cat /etc/hostname",                 "'/tmp/cat': name the program, or give its path in"),
    ("./cat /etc/hostname",                    "'./cat': name the program"),
    ("/usr/bin/../../tmp/cat x",               "name the program, or give its path in"),
    ("uptime extra",                           "uptime: at most 0 operands allowed"),
    ("cat 'unbalanced",                        "could not parse command: unbalanced single quote"),
    ('cat "unbalanced',                        "could not parse command: unbalanced double quote"),
    ("cat foo\\",                              "could not parse command: trailing backslash"),
    ("",                                       "empty command"),
    ("   ",                                    "empty command"),
    ("x" * 4001,                               "command too long"),
]


@pytest.mark.parametrize("cmd,needle", REFUSED)
def test_refused_with_reason(cmd, needle):
    ok, reason = ops.classify_readonly(cmd)
    assert not ok, f"{cmd!r} should be refused"
    assert needle in reason, f"reason {reason!r} should contain {needle!r}"


@pytest.mark.parametrize("pattern", ops.DENIED_PATH_PATTERNS)
def test_every_deny_pattern_refuses_something(pattern):
    # Turn the pattern into a concrete name and check cat refuses it, so a
    # pattern that silently matches nothing is caught.
    name = pattern.replace("*", "x")
    ok, reason = ops.classify_readonly(f"cat /home/pi/{name}")
    assert not ok and ops.DENIED_PATH_PATTERNS and "deny list" in reason


def test_deny_list_is_one_constant():
    assert isinstance(ops.DENIED_PATH_PATTERNS, tuple)
    for p in (".ssh", "id_*", ".env", "shadow*"):
        assert p in ops.DENIED_PATH_PATTERNS


def test_removed_programs_are_not_on_the_allowlist():
    for prog in ("env", "printenv", "docker"):
        assert prog not in ops._RULES


def test_dual_use_program_needs_readonly_subcommand():
    assert ops.classify_readonly("systemctl --user show magicmirror")[0]
    assert not ops.classify_readonly("systemctl --user mask magicmirror")[0]
    assert ops.classify_readonly("git -C /x status")[0]
    assert not ops.classify_readonly("git -C /x pull")[0]
    assert not ops.classify_readonly("git -C /x push")[0]
    assert not ops.classify_readonly("git -C /x checkout main")[0]


def test_lexer_matches_bash_word_splitting():
    """For quoted input, the lexer's words must be the words bash passes
    after quote removal (eval with globbing off)."""
    cases = [
        'grep -nE "a|b" f',
        "grep 'x y' \"a\\\"b\" c\\ d",
        "echo a'b'\"c\"d",
        'echo "back\\slash" "q\\\\"',
        "cat '' x",
        "cat \\;",
        "cat /home/pi/.s\\sh/id_rsa",
    ]
    for cmd in cases:
        bash = subprocess.run(
            ["bash", "-c", 'set -f; eval "set -- $1"; printf "%s\\0" "$@"', "_", cmd],
            capture_output=True, text=True, check=True)
        assert ops._lex(cmd) == [bash.stdout.split("\0")[:-1]], cmd


# ── registry ──────────────────────────────────────────────────────────────────

def test_registry_env_fallback(monkeypatch):
    monkeypatch.setattr(ops, "OPS_HOSTS_FILE", ops.Path("/nonexistent.json"))
    monkeypatch.setenv("MM_SSH_TARGET", "pi@mirror")
    monkeypatch.setenv("MM_SSH_KEY", "/keys/k")
    reg = ops.load_registry()
    assert reg["magicmirror"]["ssh_target"] == "pi@mirror"
    assert "sudo" not in reg["magicmirror"]     # dead field removed in M3


def test_registry_file_wins(monkeypatch, tmp_path):
    f = tmp_path / "hosts.json"
    f.write_text(json.dumps({"nas": {"ssh_target": "u@nas", "key": "/keys/n"}}))
    monkeypatch.setattr(ops, "OPS_HOSTS_FILE", f)
    monkeypatch.delenv("MM_SSH_TARGET", raising=False)
    reg = ops.load_registry()
    assert "nas" in reg


def test_example_registry_has_no_sudo_field():
    example = json.loads((ops.Path(__file__).parent.parent / "ops"
                          / "hosts.example.json").read_text())
    for name, entry in example.items():
        if isinstance(entry, dict):
            assert "sudo" not in entry, name


def test_audit_writes_line(monkeypatch, tmp_path):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(ops, "OPS_AUDIT_LOG", log)
    ops.audit_exec("magicmirror", "uptime", 0, 42, allowed=True)
    ops.audit_exec("magicmirror", "reboot", None, 0, allowed=False, note="refused")
    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert len(lines) == 2
    assert lines[0]["allowed"] is True and lines[1]["allowed"] is False


# ── /ops/exec route ───────────────────────────────────────────────────────────

@pytest.fixture
def ops_client(tmp_path, monkeypatch):
    import tool_service.main as main_mod
    monkeypatch.setattr(main_mod.ops, "OPS_AUDIT_LOG", tmp_path / "audit.log")
    monkeypatch.setattr(main_mod.ops, "OPS_HOSTS_FILE", tmp_path / "nohosts.json")
    monkeypatch.setenv("MM_SSH_TARGET", "pi@mirror")
    monkeypatch.setenv("MM_SSH_KEY", str(tmp_path / "key"))
    (tmp_path / "key").write_text("k")
    return TestClient(main_mod.app)


def test_ops_exec_runs_readonly(ops_client, monkeypatch):
    import tool_service.main as main_mod

    async def fake_run(cmd, timeout_s):
        assert cmd[-2:] == ["pi@mirror", "uptime -p"]   # host + command last
        return 0, "up 3 days, 4 hours"
    monkeypatch.setattr(main_mod, "_run", fake_run)

    r = ops_client.post("/ops/exec", json={"host": "magicmirror", "command": "uptime -p"})
    assert r.status_code == 200
    body = r.json()
    assert body["exit_code"] == 0
    assert "up 3 days" in body["output"]


def test_ops_exec_refuses_mutation_with_422(ops_client):
    r = ops_client.post("/ops/exec",
                        json={"host": "magicmirror", "command": "sudo reboot"})
    assert r.status_code == 422
    assert "refused" in r.json()["detail"]


def test_ops_exec_refusal_is_audited_with_reason_and_never_runs(
        ops_client, monkeypatch, tmp_path):
    import tool_service.main as main_mod

    async def must_not_run(cmd, timeout_s):
        raise AssertionError("a refused command reached _run")
    monkeypatch.setattr(main_mod, "_run", must_not_run)

    cmd = "cat /home/pi/.ssh/id_rsa"
    r = ops_client.post("/ops/exec", json={"host": "magicmirror", "command": cmd})
    assert r.status_code == 422
    _, reason = ops.classify_readonly(cmd)
    assert r.json()["detail"] == f"refused: {reason}"
    (line,) = [json.loads(l) for l in (tmp_path / "audit.log").read_text().splitlines()]
    assert line["command"] == cmd and line["allowed"] is False
    assert line["note"] == reason and line["exit_code"] is None


def test_ops_exec_unknown_host_404(ops_client):
    r = ops_client.post("/ops/exec", json={"host": "kronk", "command": "uptime"})
    assert r.status_code == 404
    assert "unknown host" in r.json()["detail"]
