#!/usr/bin/env bash
# sandbox-probe.sh - check from inside a wrapper that the sandbox rules hold
# (process/sandbox.md). Run it as the first command of a wrapper, before a step.
#
# Usage: scripts/sandbox-probe.sh --net <off|on> --work <dir> --real-home <dir>
#            --deny <path>... [--allow-env <name>]... [--proc <dir>]
#
#   --net        off: the network is checked; on: it is not (a CLI that calls a model API)
#   --work       the work folder; it must accept a write, and its parent must not
#   --real-home  the home of the host user; HOME must not be it
#   --deny       paths that must be absent or empty (keys, config, state, logs);
#                1 or more paths after the option, and the option may repeat
#   --allow-env  a variable that the wrapper sets on purpose; may repeat
#   --proc       a folder that replaces /proc for self/status and net/dev; a seam
#                for the tests, never used in a wrapper check
#
# Checks: `sudo -n true` fails; NoNewPrivs is 1; each deny path is absent or
# empty; a write in the work folder works and 1 in its parent fails; with
# --net off only the interface lo exists; no variable is set outside PATH, HOME,
# TMPDIR, LANG, PWD, SHLVL, _ and the --allow-env names; HOME is set and is not
# the real home. No network call. Nothing is printed of a value or a file.
#
# Output: 1 line "check failed: ..." for each failed check, then
#   SANDBOX: PASS              (exit 0)
#   SANDBOX: FAIL - <n> checks (exit 1)
# A usage error prints the usage to stderr and exits 2.
#
# shortcut: an exported shell function, and an environment entry whose name is
# not a shell identifier (such as A-B), are not seen; TMPDIR is allowed with any
# value. Upgrade if a wrapper can pass one. Linux only (/proc). Portable to bash 3.2: no associative arrays,
# grep -E only (no -P). Do not `export` or `cd` in this shell: the variable
# check reads the exported names, and `cd` would add OLDPWD.

net=""
work=""
real_home=""
proc=/proc
denies=()
allow=()

usage() {
    echo "usage: sandbox-probe.sh --net <off|on> --work <dir> --real-home <dir> --deny <path>... [--allow-env <name>]... [--proc <dir>]" >&2
    exit 2
}

while [ $# -gt 0 ]; do
    case "$1" in
        --net)       [ $# -ge 2 ] || usage; net=$2; shift 2 ;;
        --work)      [ $# -ge 2 ] || usage; work=$2; shift 2 ;;
        --real-home) [ $# -ge 2 ] || usage; real_home=$2; shift 2 ;;
        --proc)      [ $# -ge 2 ] || usage; proc=$2; shift 2 ;;
        --allow-env) [ $# -ge 2 ] || usage
                     case "$2" in ''|[0-9]*|*[!A-Za-z0-9_]*) usage ;; esac
                     allow[${#allow[@]}]=$2; shift 2 ;;
        --deny)      shift; n=0
                     while [ $# -gt 0 ] && [ "${1#--}" = "$1" ]; do
                         denies[${#denies[@]}]=$1; n=$((n + 1)); shift
                     done
                     [ "$n" -gt 0 ] || usage ;;
        *)           usage ;;
    esac
done
case "$net" in off|on) ;; *) usage ;; esac
[ -n "$work" ] && [ -n "$real_home" ] && [ "${#denies[@]}" -gt 0 ] || usage

fails=0
fail() { echo "check failed: $1"; fails=$((fails + 1)); }

# The variable check comes first: the helpers below add no exported variable.
extra=""
for v in $(compgen -e); do
    case " PATH HOME TMPDIR LANG PWD SHLVL _ ${allow[*]} " in
        *" $v "*) ;;
        *) extra="$extra $v" ;;
    esac
done
[ -z "$extra" ] || fail "variables outside the list:$extra"

# 1. No privilege gain.
if sudo -n true </dev/null >/dev/null 2>&1; then fail "sudo -n true succeeded"; fi
grep -E '^NoNewPrivs:[[:space:]]+1[[:space:]]*$' "$proc/self/status" >/dev/null 2>&1 \
    || fail "NoNewPrivs is not 1"

# 2. Files: each deny path is absent or empty.
i=0
for p in "${denies[@]}"; do
    i=$((i + 1))
    if [ -d "$p" ]; then
        listing=$(ls -A -- "$p" 2>/dev/null) && [ -z "$listing" ] || fail "deny path $i is a folder that is not empty"
    elif [ -f "$p" ]; then
        [ ! -s "$p" ] || fail "deny path $i is a file that is not empty"
    elif [ "$p" -ef /dev/null ]; then
        :  # the usual mask of a file: it reads as empty
    elif [ -e "$p" ]; then
        fail "deny path $i exists and is not a file or a folder"
    fi
done

# 3. Files: a write in the work folder works, 1 in its parent fails.
work=$(cd "$work" && pwd -P) || usage
if t=$(mktemp "$work/.sandbox-probe.XXXXXX" 2>/dev/null); then
    rm -f -- "$t"
else
    fail "cannot write in the work folder"
fi
parent=$(dirname -- "$work")
if t=$(mktemp "$parent/.sandbox-probe.XXXXXX" 2>/dev/null); then
    rm -f -- "$t"
    fail "the parent of the work folder is writable"
fi

# 4. Network: with --net off only lo is listed.
if [ "$net" = off ]; then
    if [ ! -r "$proc/net/dev" ]; then
        fail "network not off: cannot read net/dev"
    else
        other=$(sed -n -E 's/^[[:space:]]*([^[:space:]|:]+):.*/\1/p' "$proc/net/dev" | grep -v -x lo)
        [ -z "$other" ] || fail "network not off: an interface other than lo"
    fi
fi

# 5. Temporary places: HOME is set and is not the real home.
if [ -z "${HOME:-}" ]; then
    fail "HOME is not set"
elif [ "${HOME%/}" = "${real_home%/}" ] || [ "$HOME" -ef "$real_home" ]; then
    fail "HOME is the real home"
fi

if [ "$fails" -eq 0 ]; then
    echo "SANDBOX: PASS"
    exit 0
fi
echo "SANDBOX: FAIL - $fails checks"
exit 1
