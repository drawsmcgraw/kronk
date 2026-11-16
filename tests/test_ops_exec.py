"""Tests for the general-ops read-only classifier + registry + /ops/exec.

The classifier IS the safety boundary (docs/plans/MAGICMIRROR_PLAN.md
"General ops agent"; hardened in sprint item M3, docs/sprint/M3-build.md,
and again after its end-to-end test, docs/sprint/M3-fix1.md).
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
    ("grep --rec x /home",                     "long options must be written in full"),   # fix 1: no abbreviations
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
    ("ls 2>/tmp/err",                          "'>' (redirection)"),       # 2>/dev/null and 2>&1: see FIX1_ALLOWED
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


# ── M3 fix 1 (docs/sprint/M3-fix1.md) ─────────────────────────────────────────
# The end-to-end test (docs/sprint/M3-e2e.md) found six ways past the M3
# classifier. Each was fixed as a class: one character set (F1), closed
# option tables (F2), path rules on every argument (F3, F5), a remote
# timeout (F4, F5), no option that names a write location (F6).

# Forms the M3 build refused and fix 1 allows again, each without reopening
# a class, plus forms that pin Drew's deny-list ruling of 2026-11-16.
FIX1_ALLOWED = [
    # the two stderr words (the whole redirection grammar)
    "ls 2>&1",
    "ls 2>/dev/null",
    "2>&1 uptime",
    "ls /home/pi/MagicMirror/modules/default/updatenotification 2>/dev/null",   # audit log form 21
    'grep -r "location" /home/pi/MagicMirror/modules/default/weather/ 2>/dev/null | head -5',  # form 41
    "systemctl status x 2>&1 | head -20",
    "pm2 logs --nostream 2>&1 | tail -n 20",
    "ls '2>&1'",                                # quoted: an ordinary operand
    # globs, only in path operands of programs that print names, not contents
    "ls /var/log/*.log",
    "ls ~/.pm2/logs/*",
    "du -sh ~/MagicMirror/*",
    "du -sh ./*",
    "stat -c %s /var/log/syslog.?",
    "ls -la /dev/video*",
    "ls '/var/log/*.log'",                      # quoted: a literal
    "grep -r --include '*.js' x /home/pi/MagicMirror/js",
    # device files and '..' are harmless to programs that read no contents
    "ls -la /dev",
    "lsblk /dev/mmcblk0",
    "df -h /dev/root",
    "stat /dev/zero",
    "ls ..",
    "echo /dev/zero",                           # inert text
    "cat /dev/null",
    # pm2 describe and its two aliases
    "pm2 describe mm",
    "pm2 show 0",
    "pm2 info magicmirror",
    "pm2 logs --nostream --lines=20 --err",
    # systemctl list filters before the subcommand
    "systemctl --failed",
    "systemctl --user --failed",
    "systemctl --type=service list-units",
    "systemctl list-units --type service --state failed",
    # option values are read where the program reads them
    "ps axo pid,etime,cmd",
    "uniq -f 1 x",
    "uniq --skip-fields 1 x",
    "head -n -5 x",
    "grep -e -v x",
    "grep -n -i updatenotification -A6 /home/pi/MagicMirror/config/config.js",   # no -r: options may follow
    "journalctl -b -1 -n 20 --no-pager",
    "journalctl -n 50",
    # tagged F2 in the test table, but grep reads these as the classifier
    # now does: `never` is the pattern and BEGIN the file (--color takes a
    # value only as --color=WHEN), so nothing outside ./BEGIN is searched.
    "grep -r --color never BEGIN",
    "grep -r --colour never BEGIN",
    # git diff inside the recursive-read floor
    "git diff HEAD~1 HEAD",
    "git diff --cached",
    "git -C /home/pi/MagicMirror diff HEAD~1 -- package.json",
    "git -C /opt/app diff origin/master..HEAD",
    # Drew's ruling: config.js is readable, grep -r is fine inside the floor
    "cat /home/pi/MagicMirror/config/config.js",
    "grep -r '' /home/pi/MagicMirror",
    "systemctl status wpa_supplicant",          # the unit, not the .conf
]

# The tester's "should stay allowed" group, minus rows already in a table
# above. `ls<TAB>-la` from that group is refused now (tab is not printable
# ASCII) and sits in FIX1_REFUSED.
E2E_KEEP_ALLOWED = [
    'git -C ~/MagicMirror log --oneline -5',
    'git -C x rev-parse HEAD',
    'git -C x describe --tags',
    'git -C x branch -a',
    'git -C x branch -vv',
    'git -C x branch --show-current',
    'git -C x tag -l',
    "git -C x tag --list 'v2*'",
    'git -C x remote -v',
    'git -C x remote get-url origin',
    'git -C x show --stat HEAD',
    'git -C x log HEAD~3..HEAD',
    "git -C x log -1 --format='%h %s'",
    'git -C x diff HEAD~1 --stat',
    'git -C x status -sb',
    'git -C x ls-files modules',
    'git -C x blame -L 1,5 package.json',
    'git -C x log --follow -- package.json',
    'git -C x log --no-merges -5',
    'git -C x log --oneline --graph -10',
    'git --version',
    'git --no-pager -C x log -1',
    'journalctl -n100 --no-pager',
    'journalctl -u ssh -n 50 --no-pager',
    'journalctl -b -p err --no-pager',
    'journalctl -k -n 50',
    'journalctl -xe --no-pager',
    'journalctl --disk-usage',
    'journalctl --list-boots',
    "journalctl --since '1 hour ago' --until now --no-pager",
    'journalctl -u x --cursor abc -n 5',
    'journalctl -b -1 -n 20',
    'journalctl -r -n 20',
    'journalctl -o json -n 1',
    'journalctl -g error -n 10',
    'tail -50 /var/log/syslog',
    'tail -c 200 x',
    'tail -n +5 x',
    'head -n 20 x',
    'head -c 100 x',
    'pm2 logs mm --nostream --lines 50',
    'pm2 ls',
    'pm2 status',
    'pm2 --version',
    'pm2 logs --nostream',
    'pm2 logs --lines 50 --nostream',
    'pm2 logs --nostream --err --lines 20',
    "grep 'sudo reboot; rm -rf /' x",
    'grep ">" x',
    "grep -E 'a|b' x",
    'grep -rn x /home/pi/MagicMirror/js/defaults.js',
    'grep -rn pat ~/MagicMirror/config',
    'grep -c error x',
    'grep -v tmpfs',
    'grep -A3 -B3 x y',
    "grep -n 'foo(' x",
    'grep -o x y',
    "grep -P '\\d+' x",
    'grep --color=never x y',
    'grep -i error /var/log/syslog | tail -n 20',
    'zgrep error /var/log/syslog.2.gz',
    'ps -eo pid,etime,cmd',
    'ps aux --sort=-%mem | head',
    'ps -p 123 -o etime=',
    'ps -u pi',
    'ps axo pid,etime,cmd',
    'ps -eo pid,comm,%mem --sort -%mem',
    'ps axjf',
    'ps -C node -o pid=',
    'ps faux',
    'ps -e',
    'ps ax | wc -l',
    'ps -o pid,ppid,cmd -p 1',
    'ss -tulpn',
    'hostname -f',
    'ip -br a',
    'ip -4 addr show wlan0',
    'ip addr show dev wlan0',
    'ip route',
    'ip r',
    'ip route show',
    'ip -s link',
    'ip link show eth0',
    'ip -j addr',
    'date +%s',
    'date -u +%FT%TZ',
    'date -d yesterday +%F',
    'date -d @0',
    'date -r /etc/hostname',
    'date -R',
    'date --iso-8601=seconds',
    'date --date=yesterday',
    'date --date yesterday',
    'systemctl status magicmirror',
    'systemctl is-active x',
    'systemctl is-enabled x',
    'systemctl list-units --type=service --state=running',
    'systemctl list-units --failed',
    'systemctl list-timers',
    'systemctl status x --no-pager -l',
    'systemctl --no-pager status x',
    'systemctl show -p ActiveState x',
    'systemctl cat x',
    'systemctl status',
    'systemctl is-failed x',
    'systemctl list-unit-files',
    "stat -c '%y %n' x",
    'ls -lt --time-style=full-iso x',
    'ls -la ~',
    'ls ~/MagicMirror/modules | wc -l',
    'ls -lt | head',
    'wc -l x',
    'cat /proc/cpuinfo',
    'cat /proc/meminfo',
    'cat /sys/class/thermal/thermal_zone0/temp',
    'cat /proc/loadavg',
    'cat /etc/hostname',
    'cat /var/log/syslog | tail -n 5',
    'sort x | uniq -c | sort -rn | head',
    'sort -k2 -n x',
    'sort -t: -k3 -n /etc/passwd',
    'sort -u x',
    'sort -rn',
    'uniq -c',
    'uniq x',
    'uniq -f1 x',
    'cut -d: -f1 /etc/passwd',
    "cut -d' ' -f1 x",
    "tr -s ' '",
    'column -t x',
    'nl x',
    'npm -v',
    'npm ls',
    'npm list -g --depth=0',
    'npm root -g',
    'npm prefix',
    'npm outdated',
    'node -v',
    'vcgencmd get_mem arm',
    'vcgencmd measure_volts core',
    'vcgencmd version',
    'uname -a',
    'id pi',
    'whoami',
    'which node',
    'type -a node',
    'echo hello',
    "printf '%s\\n' x",
    'lsblk',
    'lsblk -f',
    'lscpu',
    'lsusb',
    'lsmod | grep bcm',
    'pgrep -a node',
    'pgrep -fl electron',
    'pidof node',
    'vmstat -s',
    'iostat',
    'iostat -x',
    'file x',
    'readlink -f x',
    'realpath .',
    'basename /a/b',
    'dirname /a/b',
    'true',
    '/bin/cat /etc/hostname',
    'cat \\;',
    "cat 'a b'",
    'cat a\\ b',
    'ls -la /home/pi/Magic\\ Mirror',
    "grep 'it''s' x",
    'echo "a\\"b"',
    '  ls  ',
    'ls | grep x | wc -l',
]


@pytest.mark.parametrize("cmd", FIX1_ALLOWED + E2E_KEEP_ALLOWED)
def test_fix1_allowed(cmd):
    ok, reason = ops.classify_readonly(cmd)
    assert ok, f"{cmd!r} should be allowed, got: {reason}"


# Every command the test table marks F1 to F6 (docs/sprint/M3-e2e.md, "Full
# table of attempts"), verbatim, with the reason it is refused for now.
E2E_BYPASSES = [
    # F4
    ('pm2 logs --lines --nostream', '--lines takes a number'),
    ('pm2 list --watch', 'no options or operands are allowed'),
    ('pm2 ls --watch', 'no options or operands are allowed'),
    ('pm2 status --watch', 'no options or operands are allowed'),
    # F2
    ('grep -r --exclude-dir zzz ^', 'recursive search with no directory'),
    ('grep -r --exclude-dir zzz BEGIN', 'recursive search with no directory'),
    ('grep -r --include zzz BEGIN', 'recursive search with no directory'),
    ('grep -r --exclude zzz BEGIN', 'recursive search with no directory'),
    ('grep -r --exclude-from /dev/null BEGIN', 'recursive search with no directory'),
    ('grep -r --binary-files text BEGIN', 'recursive search with no directory'),
    ('grep -r --devices skip BEGIN', 'sets the device action'),
    # F2 tag, not a bypass: 'grep -r --color never BEGIN' stays allowed (see FIX1_ALLOWED)
    # F2 tag, not a bypass: 'grep -r --colour never BEGIN' stays allowed (see FIX1_ALLOWED)
    ('grep -r --exclude-dir zzz -h ^', 'recursive search with no directory'),
    ('grep -rh --exclude-dir zzz PRIVATE', 'recursive search with no directory'),
    ('grep -r --binary-files text -A 60 OPENSSH', 'recursive search with no directory'),
    ('egrep -r --exclude-dir zzz ^', 'recursive search with no directory'),
    ('fgrep -r --exclude-dir zzz BEGIN', 'recursive search with no directory'),
    ('grep --exclude-dir zzz -r a', 'recursive search with no directory'),
    ('grep -r --exclude-dir /home/pi/a ^ ', 'recursive search with no directory'),
    ('grep -r --exclude-dir zzz -e ^', 'recursive search with no directory'),
    # F3
    ('git diff /mnt /home/pi', 'is too broad'),
    ('git diff /usr/local/games /home/pi', 'is too broad'),
    ('git diff /mnt ~', 'is too broad'),
    ('git diff /mnt .', 'is too broad'),
    ('git diff -- /mnt /home/pi', 'is too broad'),
    ('git --no-pager diff /mnt /home/pi', 'is too broad'),
    ('git -C /home/pi/MagicMirror diff /mnt /home/pi', 'is too broad'),
    ('git diff /dev/null /home/pi/.bash_history', 'secrets deny list'),
    ('git diff --stat /mnt /home/pi', 'is too broad'),
    ('git diff /mnt /etc', 'is too broad'),
    ('git diff /mnt /root', 'is too broad'),
    ('git diff /mnt /etc/ssl/private', 'is too broad'),
    ('git diff --name-only /mnt /home/pi', 'is too broad'),
    ('git diff /mnt /', 'is too broad'),
    ('git diff /etc/hostname /etc/hosts', 'is too broad'),
    ('git --git-dir=/nonexistent diff /mnt /home/pi', 'is too broad'),
    ('git --work-tree=/ diff /mnt /home/pi', 'is too broad'),
    # F5
    ('ss -E', 'prints sockets as they close'),
    ('ss --events', 'prints sockets as they close'),
    ('ss -tE', 'prints sockets as they close'),
    ('ss --ev', 'prints sockets as they close'),
    ('free -c 5', 'repeats the report'),
    ('free -c 999999', 'repeats the report'),
    ('free --count 5', 'repeats the report'),
    # F1
    ('uniq /etc/hostname \xa0', 'outside printable ASCII'),
    ('uniq /etc/hostname \u2003', 'outside printable ASCII'),
    ('uniq /etc/hostname \x1f', 'newline or control character'),
    ('uniq /etc/hostname \x1c', 'newline or control character'),
    ('uniq /etc/hostname \u3000', 'outside printable ASCII'),
    ('uniq /etc/hostname \x85', 'outside printable ASCII'),
    ('uniq /etc/hostname \u2028', 'outside printable ASCII'),
    ('hostname \xa0', 'outside printable ASCII'),
    ('date \u2003', 'outside printable ASCII'),
    ('vmstat \xa0', 'outside printable ASCII'),
    ('uniq /etc/hostname \x1d', 'newline or control character'),
    ('uniq /etc/hostname \x1e', 'newline or control character'),
    ('ls \x1f', 'newline or control character'),
    ('uniq a \x0b', 'newline or control character'),
    ('uniq a \x0c', 'newline or control character'),
    ('uniq a \n', 'newline or control character'),
    ('uniq a \r', 'newline or control character'),
    # F5
    ('cat /dev/zero', 'is a device file'),
    ('cat /dev/urandom', 'is a device file'),
    ('cat /dev/random', 'is a device file'),
    ('wc -l /dev/zero', 'is a device file'),
    ('wc /dev/urandom', 'is a device file'),
    ('sort /dev/urandom', 'is a device file'),
    ('sort /dev/zero', 'is a device file'),
    ('grep x /dev/zero', 'is a device file'),
    ('grep -a x /dev/urandom', 'is a device file'),
    ('head -n 1 /dev/zero', 'is a device file'),
    ('tail -n 1 /dev/zero', 'is a device file'),
    ('tail -c +1 /dev/zero', 'is a device file'),
    ('uniq /dev/zero', 'is a device file'),
    ('nl /dev/zero', 'is a device file'),
    ('cut -c1 /dev/zero', 'is a device file'),
    ('column /dev/zero', 'is a device file'),
    ('cat /dev/tty', 'is a device file'),
    ('cat /dev/ttyAMA0', 'is a device file'),
    ('cat /dev/input/event0', 'is a device file'),
    ('cat /dev/kmsg', 'is a device file'),
    ('cat /proc/kmsg', 'is a /proc entry'),
    ('cat /dev/watchdog', 'is a device file'),
    ('head -c 99999999999 /dev/zero', 'is a device file'),
    ("grep -c '' /dev/zero", 'is a device file'),
    ('zgrep x /dev/zero', 'is a device file'),
    ('cat /sys/kernel/debug/tracing/trace_pipe', 'kernel trace file'),
    ('date -f /dev/zero', 'is a device file'),
    ('du --files0-from=/dev/zero', 'reads the list of files'),
    ('ps -L --forest | cat /dev/zero', 'is a device file'),
    ('ss -tlnp | wc /dev/zero', 'is a device file'),
    # F6
    ('npm ls --logs-dir=/home/pi/x', 'is not on the allowed list'),
    ('npm ls --logs-dir /home/pi/MagicMirror/modules', 'is not on the allowed list'),
    ('npm ls --cache=/home/pi/x', 'is not on the allowed list'),
    ('npm ls --cache /home/pi/y', 'is not on the allowed list'),
    ('npm view x --cache /home/pi/z', 'is not on the allowed list'),
    ('npm outdated --cache=/home/pi/q', 'is not on the allowed list'),
    ('npm ls --loglevel=silly --logs-dir=/dev/shm/x', 'is a device file'),
    # F1
    ('git -C /home/pi/MagicMirror branch \xa0', 'outside printable ASCII'),
    ('git -C /home/pi/MagicMirror tag \xa0', 'outside printable ASCII'),
    ('git branch \u2003', 'outside printable ASCII'),
    ('git tag \x1f', 'newline or control character'),
    # in the report's text but not its table
    ("uniq /etc/hostname   ",              "outside printable ASCII"),
    ("grep -rh --binary-files text -A 60 OPENSSH", "recursive search with no directory"),
    ("free --count 999999",                     "repeats the report"),
    ("npm view x --registry=http://192.0.2.1/", "is not on the allowed list"),
    ("npm view x --proxy=http://192.0.2.1",     "is not on the allowed list"),
    ("npm ls --userconfig=/home/pi/x",          "is not on the allowed list"),
    ("pm2 list --no-daemon",                    "no options or operands are allowed"),
    ("pm2 --no-daemon",                         "pm2: put the subcommand first"),
    ("du --files0-from=/tmp/list",              "reads the list of files"),
]

# Siblings of each finding: the same class, in other programs and spellings.
FIX1_REFUSED = [
    # F1 class: the classifier and the shell must read the same string
    ("ls\t-la",                                 "newline or control character (0x09"),
    ("ls -la",                             "outside printable ASCII (U+00A0 at position 3)"),
    (" uniq a b",                          "outside printable ASCII"),
    ("ls café",                            "outside printable ASCII"),
    ("ls ； rm x",                          "outside printable ASCII"),
    ("ls \x7f",                                 "newline or control character"),
    ("uniq a b ",                               "uniq: at most one file operand"),   # trailing space kept, not hidden
    (" hostname x",                             "hostname: an operand sets the hostname"),
    ("\t",                                      "newline or control character"),

    # F2 class: closed option tables; long options in full; values where
    # the program reads them
    ("grep -r --include zzz BEGIN",             "recursive search with no directory"),
    ("grep -r -A 3 x",                          "recursive search with no directory"),
    ("grep -r --color=never x",                 "recursive search with no directory"),
    ("grep -r --max-count 1 x",                 "recursive search with no directory"),
    ("grep --zzz x /var/log/syslog",            "grep: option '--zzz' is not on the allowed list for grep"),
    ("grep --rec x /home/pi/MagicMirror",       "long options must be written in full"),
    ("tail --line=5 x",                         "long options must be written in full"),
    ("ls --al",                                 "ls: option '--al' is not on the allowed list"),
    ("cat -Q x",                                "cat: option -Q is not on the allowed list for cat"),
    ("ls -la~ /x",                              "ls: option -~ (in '-la~') is not on the allowed list"),
    ("stat -c -x y",                            "stat: -c takes a value, and the next word '-x' starts with '-'"),
    ("sort -k -o out x",                        "sort: -k takes a value, and the next word '-o' starts with '-'"),
    ("grep -e",                                 "grep: -e needs a value"),
    ("cat --number=1 x",                        "cat: --number takes no value"),
    ("uniq in -c",                              "uniq: put options before the operands"),
    ("uniq in --count",                         "uniq: put options before the operands"),
    # with POSIXLY_CORRECT grep stops at the first operand: "-f" and
    # "/home/pi" would then be files, searched recursively
    ("grep -r x a -f /home/pi",                 "grep: put options before the operands (got '-f' after 'x')"),
    ("grep x /home/pi/MagicMirror -r",          "grep: put options before the operands"),
    ("grep x /home/pi --recursive",             "grep: put options before the operands"),
    ("systemctl -t service --failed",           "systemctl: put options after the subcommand"),
    ("systemctl status --zzz x",                "systemctl: option '--zzz' is not on the allowed list"),
    ("ps --zzz",                                "ps: option '--zzz' is not on the allowed list"),
    ("ps -o",                                   "ps: -o needs a value"),
    ("ps -eK",                                  "ps: option letter 'K' in '-eK' is not on the allowed list"),
    ("ps axe",                                  "ps: BSD option 'axe' includes 'e'"),
    ("ip addr show -batch /tmp/x",              "ip addr: option '-batch' after the object is not allowed"),
    ("git log --zzz",                           "git log: option '--zzz' is not on the allowed list"),
    ("git log --date short",                    "git log: write --date=VALUE"),
    ("git show --textconv HEAD:x",              "git show: option '--textconv' is not on the allowed list"),
    ("git cat-file --textconv HEAD:x",          "git cat-file: option '--textconv' is not on the allowed list"),
    ("git blame -S revs x",                     "git blame: option -S is not on the allowed list"),
    ("git status --zzz",                        "git status: option '--zzz' is not on the allowed list"),
    ("git -C --no-pager log",                   "git: -C takes a directory, got '--no-pager'"),
    ("git branch -l evil",                      "git branch: an operand without --list creates a branch"),   # -l was --create-reflog before git 2.20
    ("journalctl --wait",                       "journalctl: option '--wait' is not on the allowed list"),
    ("journalctl -M host -n 5",                 "journalctl: option -M is not on the allowed list"),
    ("zgrep --color=never x y",                 "zgrep: option '--color=never' is not on the allowed list"),

    # F3 class: path rules on every path-like argument
    ("git -C / diff mnt home",                  "git diff: path operand 'mnt' is too broad"),
    ("git -C /home diff lost pi",               "is too broad"),
    ("git -C / -C home diff x/y pi",            "git diff: path operand 'pi' is too broad"),
    ("git -C ~ diff . MagicMirror",             "git diff: path operand '.' is too broad"),
    ("git diff /proc/1 /proc/2",                "recursive search under /proc"),
    ("git --work-tree=/home/pi --git-dir=/home/pi/MagicMirror/.git status", "git: --work-tree '/home/pi' is too broad"),
    ("git --work-tree / status",                "git: --work-tree '/' is too broad"),
    ("git --work-tree=/ diff HEAD",             "is too broad"),
    ("git diff a ..",                           "contains '..'"),
    ("cat /usr/bin/../../etc/os-release",       "cat: argument '/usr/bin/../../etc/os-release' contains '..'"),
    ("cat ../x",                                "contains '..'"),
    ("head -n 1 /var/run/../dev/zero",          "contains '..'"),      # /var/run is a symlink: this is /dev/zero
    ("grep x /var/lock/../../dev/zero",         "contains '..'"),
    ("journalctl --file=/var/log/../x",         "contains '..'"),
    ("sort -k1 ../x",                           "contains '..'"),
    ("grep -f../x y",                           "contains '..'"),      # attached short value
    ("git -C /home/pi/MagicMirror/.. status",   "contains '..'"),

    # F4 and F5 class: never returns. Devices and links by name...
    ("cat //dev/zero",                          "is a device file"),
    ("cat /dev/./zero",                         "is a device file"),
    ("cat /dev",                                "is a device file"),
    ("cat /dev/stdin",                          "is a device file"),
    ("cat /dev/fd/0",                           "is a device file"),
    ("wc -c /dev/full",                         "is a device file"),
    ("grep -f/dev/zero x",                      "is a device file"),
    ("grep --file=/dev/zero x",                 "is a device file"),
    ("grep x /dev/zero",                        "grep: argument '/dev/zero' is a device file"),
    ("journalctl /dev/sda",                     "is a device file"),
    ("journalctl --file /dev/sda",              "is a device file"),
    ("file /dev/sda",                           "is a device file"),
    ("date -r /dev/zero",                       "is a device file"),
    ("git --git-dir=/dev/shm/x log",            "is a device file"),
    ("git log -- /dev/zero",                    "is a device file"),
    ("cat /proc/self/root/dev/zero",            "is a /proc entry ('root')"),
    ("cat /proc/1/root/etc/hostname",           "is a /proc entry ('root')"),
    ("cat /proc/self/cwd/x",                    "is a /proc entry ('cwd')"),
    ("cat /proc/self/fd/1",                     "is a /proc entry ('fd')"),
    ("cat /proc/self/task/1/root/x",            "is a /proc entry ('root')"),
    ("head /proc/self/mem",                     "is a /proc entry ('mem')"),
    ("cat /proc/kcore",                         "is a /proc entry ('kcore')"),
    ("cat /sys/kernel/tracing/trace_pipe",      "is a kernel trace file"),
    # ...and options that follow, repeat, watch or wait
    ("tail -s 1 x",                             "tail: option -s is not on the allowed list"),
    ("tail --pid=1 x",                          "tail: option '--pid=1' is not on the allowed list"),
    ("free -hc 5",                              "free: -c repeats the report; one report only (given as '-hc')"),
    ("free --count=3",                          "free: --count repeats the report"),
    ("vmstat -n 1",                             "vmstat: an interval operand repeats"),
    ("iostat -x 1 2",                           "iostat: an interval operand repeats"),
    ("systemctl is-system-running --wait",      "systemctl: --wait blocks until the system has finished starting"),
    ("pm2 logs --nostream --timestamp",         "pm2 logs: option '--timestamp' is not on the allowed list"),
    ("pm2 logs --nostream --lines 99999",       "pm2 logs: --lines takes a number up to 9999"),
    ("pm2 logs --nostream --lines",             "pm2 logs: --lines takes a number"),
    ("pm2 logs a b --nostream",                 "pm2 logs: at most one process name or id"),
    ("pm2 describe",                            "pm2 describe: give exactly one process name or id"),
    ("pm2 describe mm --watch",                 "pm2 describe: give exactly one process name or id"),
    ("pm2 env 0",                               "pm2 'env' is not a read-only subcommand: it prints every process's environment"),
    ("grep -D read x /home/pi/MagicMirror",     "grep: -D sets the device action"),
    ("file -s /home/pi/fifo",                   "file: -s reads device files"),
    ("file -f /tmp/list",                       "file: -f reads the list of files"),
    ("wc --files0-from=/tmp/l",                 "wc: --files0-from reads the list of files"),
    ("du --files0-from=-",                      "du: --files0-from reads the list of files"),
    ("date -f /tmp/dates",                      "date: -f reads dates from a named file"),

    # F6 class: no option names a write location, a program to run, or a
    # host to contact
    ("sort -T /home/pi x",                      "sort: -T names a directory to write temporary files in"),
    ("sort --temporary-directory=/home/pi x",   "sort: --temporary-directory names a directory"),
    ("sort --random-source=/x y",               "sort: --random-source reads random bytes"),
    ("df --sync",                               "df: --sync calls sync()"),
    ("npm ls --prefix /x",                      "npm ls: option '--prefix' is not on the allowed list"),
    ("npm ls --script-shell=sh",                "npm ls: option '--script-shell=sh' is not on the allowed list"),
    ("npm ls --depth --json",                   "npm ls: --depth takes a number"),
    ("npm view ./x",                            "npm view: operand './x' is not a registry package name"),
    ("npm view user/repo",                      "is not a registry package name"),     # GitHub shorthand: a clone
    ("npm view git+ssh://x/y.git",              "is not a registry package name"),
    ("npm view http://192.0.2.1/x.tgz",         "is not a registry package name"),
    ("npm root x",                              "npm root: no operands are allowed"),
    ("zgrep -f /tmp/p x",                       "zgrep: option -f is not on the allowed list"),     # zgrep -f copies patterns to a temp file
    ("zgrep -r x /home/pi/MagicMirror",         "zgrep: option -r is not on the allowed list"),
    ("sensors -c /tmp/x",                       "sensors: -c reads a named configuration file"),
    ("ss -F /tmp/f",                            "ss: -F reads the filter from a named file"),
    ("git blame --contents /tmp/x y",           "git blame: --contents reads a named file from outside the repository"),

    # globs: nowhere but a path operand of a names-only program
    ("cat /var/log/*.log",                      "unquoted '*' (glob)"),
    ("grep x /var/log/*",                       "unquoted '*' (glob)"),
    ("uniq /tmp/a*",                            "unquoted '*' (glob)"),        # two matches would be input and OUTPUT
    ("systemctl list-units mag*",               "unquoted '*' (glob)"),
    ("l* /etc",                                 "unquoted '*' (glob)"),
    ("ls -I * /x",                              "unquoted '*' (glob)"),        # an option's value
    ("ls --sort=* /x",                          "unquoted '*' (glob)"),
    ("ls *",                                    "ls: glob '*' must start with /, ./ or ~/"),
    ("du -sh *",                                "du: glob '*' must start with /, ./ or ~/"),
    ("ls ~/.s*",                                "has a component that starts with '.'"),
    ("ls /home/pi/.*",                          "has a component that starts with '.'"),
    ("ls /var/log/[a-z]*",                      "unquoted '[' (glob)"),
    ("ls ~/.ssh/*",                             f"{DENY} ('.ssh')"),
    ("stat /home/*/.ssh/id_rsa",                f"{DENY} ('.ssh')"),

    # the redirection grammar is two exact words and nothing else
    ("ls 2>&1x",                                "'>' (redirection)"),
    ("ls 2>& 1",                                "'>' (redirection)"),
    ("ls 2>&2",                                 "'>' (redirection)"),
    ("ls 2>&1-",                                "'>' (redirection)"),
    ("ls 1>&2",                                 "'>' (redirection)"),
    ("ls 22>&1",                                "'>' (redirection)"),
    ("ls x2>&1",                                "'>' (redirection)"),
    ("ls 'x'2>&1",                              "'>' (redirection)"),
    ("ls \\2>&1",                               "'>' (redirection)"),
    ("ls 2>/dev/nullx",                         "'>' (redirection)"),
    ("ls 2>/dev/null/x",                        "'>' (redirection)"),
    ("ls 2> /dev/null",                         "'>' (redirection)"),
    ("ls 2>/tmp/x",                             "'>' (redirection)"),
    ("ls 2>>/dev/null",                         "'>>' (redirection)"),
    ("ls >&2",                                  "'>' (redirection)"),
    ("ls &>/dev/null",                          "'&' (background job)"),
    ("ls 2>&1|& cat",                           "'|&'"),
    ("2>&1",                                    "empty pipeline segment"),
    ("ls | 2>/dev/null",                        "empty pipeline segment"),

    # Drew's deny-list additions (2026-11-16)
    ("cat ~/.bash_history",                     f"{DENY} ('.bash_history')"),
    ("cat /home/pi/.npmrc",                     f"{DENY} ('.npmrc')"),
    ("cat /etc/wpa_supplicant/wpa_supplicant.conf", f"{DENY} ('wpa_supplicant*.conf')"),
    ("cat /etc/wpa_supplicant/wpa_supplicant-wlan0.conf", f"{DENY} ('wpa_supplicant*.conf')"),
    ("cat /etc/NetworkManager/system-connections/home.nmconnection", f"{DENY} ('system-connections')"),
    ("grep -r psk /etc/NetworkManager/system-connections", f"{DENY} ('system-connections')"),
    ("cat /tmp/home.nmconnection",              f"{DENY} ('*.nmconnection')"),
    ("cat ~/.pm2/dump.pm2",                     f"{DENY} ('dump.pm2*')"),
    ("cat ~/.pm2/dump.pm2.bak",                 f"{DENY} ('dump.pm2*')"),
    ("ls ~/.pm2/dump.pm2",                      f"{DENY} ('dump.pm2*')"),
]


@pytest.mark.parametrize("cmd,needle", E2E_BYPASSES + FIX1_REFUSED)
def test_fix1_refused_with_reason(cmd, needle):
    ok, reason = ops.classify_readonly(cmd)
    assert not ok, f"{cmd!r} should be refused"
    assert needle in reason, f"reason {reason!r} should contain {needle!r}"


def test_e2e_bypass_table_is_pinned():
    # 101 F-tagged rows of the tester's table are refused (two more carry
    # an F2 tag and are not bypasses: see FIX1_ALLOWED); guards against
    # the table being trimmed.
    assert len({c for c, _ in E2E_BYPASSES}) >= 101


def _all_rules():
    rules = {p: r for p, r in ops._RULES.items() if isinstance(r, ops.Rule)}
    rules.update({f"git {s}": r for s, r in ops._GIT_RULES.items()})
    rules["systemctl <verb>"] = ops._SYSTEMCTL_RULE
    return rules


@pytest.mark.parametrize("prog", sorted(ops._RULES))
def test_every_program_refuses_an_unknown_option(prog):
    rule = ops._RULES[prog]
    if isinstance(rule, ops.Rule) and rule.free:
        return          # echo, printf, true: arguments are inert text
    for cmd in (f"{prog} --zzz-not-an-option", f"{prog} --zzz-not-an-option=1 x",
                f"{prog} -~"):
        ok, reason = ops.classify_readonly(cmd)
        assert not ok, f"{cmd!r} should be refused"
    for sub in (ops._GIT_RULES if prog == "git" else ()):
        ok, _ = ops.classify_readonly(f"git {sub} --zzz-not-an-option")
        assert not ok, sub


def test_rule_tables_are_consistent():
    """No option is listed twice or both allowed and forbidden, and every
    dash_ok / next_opt entry names an option that takes a value."""
    for prog, r in _all_rules().items():
        shorts = r.flags + r.short_args + r.short_opt
        assert len(set(shorts)) == len(shorts), f"{prog}: short letter listed twice"
        longs = [n for grp in (r.longs, r.long_args, r.long_eq, r.long_opt) for n in grp]
        assert len(set(longs)) == len(longs), f"{prog}: long option listed twice"
        for f in r.forbid:
            if f.startswith("--"):
                assert f[2:] not in longs, f"{prog}: {f} is allowed and forbidden"
            else:
                assert f[1] not in shorts, f"{prog}: {f} is allowed and forbidden"
        for n in r.dash_ok:
            assert (len(n) == 2 and n[1] in r.short_args) or n[2:] in r.long_args, (prog, n)
        assert set(r.next_opt) <= set(r.short_opt), prog


# Options that write a file, run a program, follow, repeat or contact a
# host. None may appear in an allowed table, whatever else changes.
NEVER_ALLOWED = {
    "sort": ["-o", "--output", "-T", "--temporary-directory", "--compress-program", "--files0-from"],
    "tail": ["-f", "-F", "--follow", "--retry", "-s", "--sleep-interval", "--pid"],
    "journalctl": ["-f", "--follow", "--rotate", "--flush", "--sync", "--vacuum-size",
                   "--vacuum-time", "--vacuum-files", "--cursor-file", "--setup-keys",
                   "--update-catalog", "--relinquish-var", "--smart-relinquish-var"],
    "free": ["-s", "--seconds", "-c", "--count"],
    "ss": ["-E", "--events", "-K", "--kill", "-D", "--diag", "-F", "--filter", "-N", "--net"],
    "file": ["-C", "--compile", "-f", "--files-from", "-s", "--special-files"],
    "date": ["-s", "--set", "-f", "--file"],
    "hostname": ["-F", "--file", "-b", "--boot"],
    "sensors": ["-s", "--set", "-c", "--config-file"],
    "grep": ["-R", "--dereference-recursive", "-d", "--directories", "-D", "--devices"],
    "zgrep": ["-r", "-R", "-f", "-d", "-D"],
    "wc": ["--files0-from"], "du": ["--files0-from"], "df": ["--sync"],
    "git log": ["--output", "--ext-diff", "--no-index", "--textconv", "--contents"],
    "git show": ["--output", "--ext-diff", "--textconv"],
    "git diff": ["--output", "--ext-diff", "--no-index", "--textconv"],
    "git blame": ["--contents", "-S"],
    "git cat-file": ["--textconv", "--filters"],
    "systemctl <verb>": ["-H", "--host", "-M", "--machine", "--wait", "--root"],
}


def test_dangerous_options_are_in_no_allowed_table():
    rules = _all_rules()
    for prog, opts in NEVER_ALLOWED.items():
        r = rules[prog]
        allowed_long = r.longs | r.long_args | r.long_eq | r.long_opt
        allowed_short = r.flags + r.short_args + r.short_opt
        for o in opts:
            if o.startswith("--"):
                assert o[2:] not in allowed_long, f"{prog} {o}"
            else:
                assert o[1] not in allowed_short, f"{prog} {o}"


def test_classifier_judges_the_exact_string():
    # F1: nothing is stripped before classifying. A trailing word made of a
    # character Python calls whitespace and bash does not is refused, not
    # dropped; ordinary spaces around a command change nothing.
    assert ops.classify_readonly("  uptime  ") == (True, "read-only")
    for ch in "  　 \u0085\x1c\x1d\x1e\x1f\x0b\x0c\n\r\t":
        ok, _ = ops.classify_readonly(f"uniq /etc/hostname {ch}")
        assert not ok, f"U+{ord(ch):04X}"
        ok, _ = ops.classify_readonly(f"uptime{ch}")
        assert not ok, f"U+{ord(ch):04X}"
    # every character outside 0x20..0x7E, anywhere in the line
    for o in list(range(0, 32)) + [127, 128, 0xA0, 0x2003, 0xFF1B]:
        assert not ops.classify_readonly(f"ls a{chr(o)}b")[0], hex(o)


def test_classifier_never_raises():
    for bad in (None, 5, 0, True, b"ls", ["ls"], {"a": 1}, 1.5):
        ok, reason = ops.classify_readonly(bad)
        assert ok is False and isinstance(reason, str) and reason


# ── lexer against bash ────────────────────────────────────────────────────────

_FUZZ_TOKENS = ["a", "b", "1", "2", " ", " ", " ", "|", "'", '"', "\\", ">", "&",
                "<", ";", "2>&1", "2>/dev/null", "/dev/null", "-", "~", "/", "#",
                "!", "%", "^", "@", ",", ".", ":", "]", "(", ")", "$", "`", "{",
                "}", "[", "*", "?", "\t", " ", "2", ">", "&1"]
# Half the lines are drawn from this shorter list, so that the stderr words
# and their near misses come up often enough to matter.
_FUZZ_REDIRECT_TOKENS = ["a", "b", " ", " ", " ", "2>&1", "2>/dev/null", "|",
                         "'", '"', "\\", "2", ">", "&1", "&", "/dev/null", "1",
                         "-", "x"]
_FUZZ_WORDS = ["a", "b", "x", "'q r'", '"s  t"', "-n", "2>&1", "2>/dev/null",
               "| a", "\\ ", "2", "1", "2>&1x", "x2>&1", "'2>&1'", "~", "c\\|d"]
# Every command is "not found" (PATH is empty), so bash hands its words to
# this handler, which records them with one write. Globbing is off: glob
# words are compared as text here and have their own tests.
_FUZZ_HANDLER = ('set -f; command_not_found_handle() { local r; '
                 'r=$(printf "%s\\037" "$@"); printf "%s\\036" "$r" >>"$CAP"; }; '
                 'eval -- "$1"')


def _bash_words(line, cwd, cap):
    open(cap, "wb").close()
    subprocess.run(["/bin/bash", "-c", _FUZZ_HANDLER, "_", line], cwd=cwd,
                   stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, timeout=20,
                   env={"PATH": "/nonexistent", "HOME": "~", "CAP": str(cap)})
    data = open(cap, "rb").read().decode()
    return sorted(tuple(rec.split("\x1f")[:-1]) for rec in data.split("\x1e")[:-1])


def test_lexer_agrees_with_bash_on_random_lines(tmp_path):
    """F1, and the proof that the redirection grammar is closed: for every
    line the lexer accepts, bash passes exactly the lexer's words to each
    command and creates no file. Lines are random strings of quotes,
    backslashes, pipes, every shell metacharacter, the two stderr words and
    their fragments, a tab and a no-break space. (20,000 compared lines in
    docs/sprint/M3-fix1.md; a fixed sample here.)"""
    import os
    import random
    builtins = set(subprocess.run(["/bin/bash", "-c", "compgen -b; compgen -k"],
                                  capture_output=True, text=True).stdout.split())
    rnd = random.Random(20261116)
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    compared = with_redirect = 0
    while compared < int(os.environ.get("OPS_LEX_FUZZ", "300")):
        mode = rnd.random()
        if mode < 0.3:      # whole words, so the stderr words land on boundaries
            line = "a " + rnd.choice(["", " "]).join(
                rnd.choice(_FUZZ_WORDS) + " " for _ in range(rnd.randint(1, 6)))
        else:
            tokens = _FUZZ_TOKENS if mode < 0.65 else _FUZZ_REDIRECT_TOKENS
            line = "".join(rnd.choice(tokens) for _ in range(rnd.randint(1, 9)))
        try:
            segs = ops._lex(line)
        except ops._Refused:
            continue
        # bash does not call the handler for a builtin, a keyword, a job
        # spec, an assignment or a name with a slash: skip, do not compare.
        if any((not s) or s[0] in builtins or s[0] == "" or "=" in s[0]
               or "/" in s[0] or s[0].startswith("%") for s in segs):
            continue
        compared += 1
        # a '>' that is in the line but in no word was taken as a stderr word
        with_redirect += line.count(">") > sum(w.count(">") for s in segs for w in s)
        want = sorted(tuple(str(w) for w in s) for s in segs)
        assert _bash_words(line, cwd, tmp_path / "cap") == want, repr(line)
        assert not list(cwd.iterdir()), f"{line!r} created a file"
    assert with_redirect >= compared // 10   # the sample does exercise the stderr words
    if os.environ.get("OPS_LEX_FUZZ"):
        print(f"\nlexer vs bash: {compared} lines compared, {with_redirect} with "
              "a stderr word, 0 differences, 0 files created")


def test_stderr_words_are_not_arguments():
    assert ops._lex("ls 2>&1 | wc -l 2>/dev/null") == [["ls"], ["wc", "-l"]]
    assert ops._lex("ls '2>&1' \"2>/dev/null\"") == [["ls", "2>&1", "2>/dev/null"]]


def test_glob_flag_only_on_unquoted_globs():
    (words,) = ops._lex("ls /a/*.log '/b/*' \"c?\" d\\* e?")
    assert [w.glob for w in words] == [False, True, False, False, False, True]


# ── remote wrapper ────────────────────────────────────────────────────────────

WRAPPED = [
    "uptime",
    "  uptime  ",
    "grep 'it''s' \"a b\" c\\ d",
    "journalctl -u x | grep -iE 'a|b' 2>&1 | tail -n 5",
    "ls /var/log/*.log",
    "echo '$HOME `id` $(id)'",
]


@pytest.mark.parametrize("cmd", WRAPPED)
def test_remote_command_carries_the_exact_string(cmd):
    """The login shell on the host must hand `bash -c` the classified
    string, byte for byte."""
    import shlex
    wrapped = ops.remote_command(cmd)
    assert shlex.split(wrapped) == ["timeout", "-k", str(ops.REMOTE_KILL_AFTER_S),
                                    str(ops.REMOTE_TIMEOUT_S), "bash", "-c", cmd]
    # and as a real shell reads it
    out = subprocess.run(["/bin/bash", "-c", f'set -- {wrapped}; printf "%s\\0" "$@"'],
                         capture_output=True, text=True, check=True).stdout
    assert out.split("\0")[:-1][-1] == cmd


def test_remote_limits_fit_inside_the_local_wait():
    import tool_service.main as main_mod
    assert ops.REMOTE_TIMEOUT_S + ops.REMOTE_KILL_AFTER_S < main_mod.OPS_EXEC_TIMEOUT_S


@pytest.fixture
def gnu_timeout_path(tmp_path):
    """A PATH whose `timeout` is GNU coreutils, which is what the managed
    hosts run. This machine's default `timeout` is uutils (it exits 125,
    not 124, with -k); GNU is installed beside it as gnutimeout."""
    import shutil
    for name in ("timeout", "gnutimeout"):
        exe = shutil.which(name)
        if exe and "GNU coreutils" in subprocess.run(
                [exe, "--version"], capture_output=True, text=True).stdout:
            bindir = tmp_path / "gnubin"
            bindir.mkdir()
            (bindir / "timeout").symlink_to(exe)
            return f"{bindir}:/usr/bin:/bin"
    pytest.skip("no GNU coreutils timeout on this machine")


def _survivors(marker):
    out = subprocess.run(["pgrep", "-f", f"sleep {marker}"],
                         capture_output=True, text=True).stdout
    return out.split()


def test_remote_timeout_kills_the_whole_pipeline(gnu_timeout_path):
    """F4/F5: a command that never returns is stopped on the host by the
    wrapper itself. Nothing here depends on the caller staying connected."""
    import random
    import time
    marker = 300000 + random.randrange(100000)
    wrapped = ops.remote_command(f"sleep {marker} | sleep {marker}", timeout_s=1)
    t0 = time.time()
    p = subprocess.run(["/bin/bash", "-c", wrapped], env={"PATH": gnu_timeout_path},
                       stdin=subprocess.DEVNULL, capture_output=True)
    assert p.returncode == 124 and p.returncode in ops.REMOTE_TIMEOUT_EXIT_CODES
    assert time.time() - t0 < 3
    assert _survivors(marker) == []


def test_remote_timeout_kills_a_process_that_ignores_term(gnu_timeout_path):
    import random
    import shlex
    import time
    marker = 400000 + random.randrange(100000)
    # built by hand: a trap is not something the classifier would allow
    wrapped = (f"timeout -k 1 1 bash -c "
               + shlex.quote(f"trap '' TERM; sleep {marker} | sleep {marker}"))
    t0 = time.time()
    p = subprocess.run(["/bin/bash", "-c", wrapped], env={"PATH": gnu_timeout_path},
                       stdin=subprocess.DEVNULL, capture_output=True)
    assert p.returncode in (137, -9)          # KILL, sent 1 s after TERM
    assert time.time() - t0 < 4
    time.sleep(0.2)
    assert _survivors(marker) == []


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
        # host, then the command inside the remote wrapper, last
        assert cmd[-2:] == ["pi@mirror", ops.remote_command("uptime -p")]
        assert cmd[-1] == "timeout -k 2 25 bash -c 'uptime -p'"
        assert cmd[:2] == ["ssh", "-n"]                 # empty stdin for the remote command
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


def test_ops_exec_sends_the_string_it_classified(ops_client, monkeypatch):
    """F1: the route classifies req.command and sends req.command, whole.
    A trailing space is part of both; nothing is stripped in between."""
    import shlex
    import tool_service.main as main_mod
    sent = []

    async def fake_run(cmd, timeout_s):
        sent.append(cmd[-1])
        return 0, ""
    monkeypatch.setattr(main_mod, "_run", fake_run)

    for cmd in ("uniq -c /etc/hostname ", "  ls -la  ", "grep 'a  b' /etc/hostname"):
        r = ops_client.post("/ops/exec", json={"host": "magicmirror", "command": cmd})
        assert r.status_code == 200, r.text
        assert shlex.split(sent[-1])[-1] == cmd
        assert r.json()["command"] == cmd
    r = ops_client.post("/ops/exec", json={"host": "magicmirror",
                                           "command": "uniq /etc/hostname \u00a0"})
    assert r.status_code == 422 and "printable ASCII" in r.json()["detail"]
    assert len(sent) == 3


@pytest.mark.parametrize("rc", [124, 137])
def test_ops_exec_remote_timeout_is_a_504_and_is_audited(ops_client, monkeypatch,
                                                         tmp_path, rc):
    import tool_service.main as main_mod

    async def fake_run(cmd, timeout_s):
        return rc, "partial output"
    monkeypatch.setattr(main_mod, "_run", fake_run)

    r = ops_client.post("/ops/exec", json={"host": "magicmirror",
                                           "command": "journalctl -n 5"})
    assert r.status_code == 504
    want = {124: "command ran past 25s on magicmirror and was stopped there (exit 124)",
            137: "command was killed on magicmirror (it ran past 25s and ignored "
                 "the stop signal, or the host killed it) (exit 137)"}[rc]
    assert r.json()["detail"] == want
    (line,) = [json.loads(l) for l in (tmp_path / "audit.log").read_text().splitlines()]
    assert line["allowed"] is True and line["exit_code"] == rc


def test_ops_exec_local_timeout_is_still_a_504(ops_client, monkeypatch):
    import tool_service.main as main_mod

    async def fake_run(cmd, timeout_s):
        assert timeout_s == main_mod.OPS_EXEC_TIMEOUT_S == 30
        return None, "timed out after 30s"
    monkeypatch.setattr(main_mod, "_run", fake_run)

    r = ops_client.post("/ops/exec", json={"host": "magicmirror", "command": "uptime"})
    assert r.status_code == 504
    assert r.json()["detail"] == "command timed out after 30s on magicmirror"
