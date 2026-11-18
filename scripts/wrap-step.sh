#!/usr/bin/env bash
# wrap-step.sh - the wrap of a Claude step on a Linux host: run the step in
# bubblewrap, by the rules of process/sandbox.md. As the wrap of a runner:
#
#   wrap = ["/absolute/path/of/scripts/wrap-step.sh", "write"]   (build, rework)
#   wrap = ["/absolute/path/of/scripts/wrap-step.sh", "read"]    (spec check, review)
#
# The step then runs `wrap-step.sh <write|read> <cli> [args...]` in the work
# folder. Inside the wrapper:
#   - the network is shared (the CLI calls the model API); the other namespaces are new;
#   - read-only: /usr (and /bin, /lib, /lib64, /sbin), a list of named /etc
#     files, the CLI and the folder of the file it links to;
#   - the work folder (the current folder): writable for write, read-only for read;
#   - <work folder>.step/home is HOME, writable, new for each step (python -m wb
#     makes it); <work folder>.step/inputs, the copies of the inputs, read-only;
#   - /tmp is private; the rest of the root is read-only and empty: no ~/.ssh,
#     ~/.config, ~/.claude, state home, code clone, other repo or env file;
#   - the environment is cleared, then only PATH, HOME, TMPDIR, LANG,
#     DISABLE_AUTOUPDATER and CLAUDE_CODE_OAUTH_TOKEN are set. The token comes
#     from $HOME/.config/workbench/claude-token (mode 0600) and goes to bwrap
#     on a file descriptor (--args), never on a command line.
#
# `wrap-step.sh write --review-token <cli> [args...]` (write mode only) also
# copies $HOME/.config/workbench/review-token (mode 0600) to the same path in the
# step home, mode 600, for a nested review CLI. The wrapper deletes the file when
# the step ends (exit or TERM; KILL cannot be trapped, so python -m wb deletes it
# after a timeout or a reboot). The review token is in no argv and no environment
# of the step.
#
# In each mode the wrapper stays while the step runs, and replaces each copy of
# the token (and of the review token) in the step's stdout and stderr with
# <redacted>: the stream of a Claude step holds the tool results, so a step that
# prints its environment would otherwise store the token in the run folder.
#
# Exit: the exit code of the step; 2 for a usage error (then bwrap does not start).
# Linux only (bwrap, stat -c); python3 for the filter of the output. bash 3.2:
# no associative arrays.

set -u
die() { echo "wrap-step: $1" >&2; exit 2; }
usage="usage: wrap-step.sh <write|read> [--review-token] <cli> [args...]"

case "${1:-}" in
    write) work_bind=--bind ;;
    read)  work_bind=--ro-bind ;;
    *)     die "$usage" ;;
esac
shift
command -v python3 > /dev/null || die "no python3 for the filter of the step output"
review=
if [ "${1:-}" = --review-token ]; then
    [ "$work_bind" = --bind ] || die "--review-token is for write mode only"
    review=1
    shift
fi
[ $# -ge 1 ] || die "$usage"

work=$(pwd -P) || die "cannot read the work folder"
step="$work.step"
[ -d "$step/home" ] && [ -d "$step/inputs" ] || die "no step folder (home and inputs): $step"
# read_token <file>: the checks of a token file, then its value in $value.
read_token() {
    [ -f "$1" ] || die "no token file: $1"
    mode=$(stat -c %a -- "$1") || die "cannot read the mode of $1"
    case "$mode" in [4-7]00) ;; *) die "the token file must have mode 0600: $1" ;; esac
    value=$(cat -- "$1") || die "cannot read $1"
    [ -n "$value" ] || die "the token file is empty: $1"
}
read_token "$HOME/.config/workbench/claude-token"
token=$value
if [ -n "$review" ]; then
    read_token "$HOME/.config/workbench/review-token"
    review_token=$value
fi
cli=$1
real=$(readlink -f -- "$cli") || die "cannot resolve the CLI: $cli"
[ -f "$real" ] || die "the CLI is not a file: $cli"

args=(--unshare-all --share-net --die-with-parent --new-session --clearenv)
for d in /bin /sbin /lib /lib64; do
    if [ -L "$d" ]; then
        args+=(--symlink "$(readlink -- "$d")" "$d")
    elif [ -d "$d" ]; then
        args+=(--ro-bind "$d" "$d")
    fi
done
args+=(--ro-bind /usr /usr --proc /proc --dev /dev --tmpfs /tmp)
for f in /etc/resolv.conf /etc/hosts /etc/nsswitch.conf /etc/passwd /etc/group /etc/localtime \
         /etc/ssl /etc/ca-certificates /etc/alternatives; do
    args+=(--ro-bind-try "$f" "$f")
done
args+=(--ro-bind "$cli" "$cli" --ro-bind "$(dirname -- "$real")" "$(dirname -- "$real")"
       "$work_bind" "$work" "$work"
       --bind "$step/home" "$step/home" --ro-bind "$step/inputs" "$step/inputs"
       --remount-ro /
       --chdir "$work"
       --setenv PATH /usr/local/bin:/usr/bin:/bin --setenv HOME "$step/home" --setenv TMPDIR /tmp
       --setenv LANG C.UTF-8 --setenv DISABLE_AUTOUPDATER 1)
# The wrapper stays while the step runs, to filter its output and delete the review file when it ends; KILL
# cannot be trapped.
step_home="$step/home"
review_file="$step_home/.config/workbench/review-token"
pid= term= fifos=
# No rm through a folder that the step made a symlink: it could point at the host's own token.
trap '[ -z "$review" ] || [ "$(readlink -f -- "${review_file%/*}")" != "${review_file%/*}" ] ||
          rm -f -- "$review_file"
      [ -z "$fifos" ] || rm -rf -- "$fifos"' EXIT
trap 'term=1; [ -z "$pid" ] || kill -TERM "$pid" 2>/dev/null' TERM
# The step's stdout and stderr go through redact by 2 fifos, so that the wrapper can wait for both.
fifos=$(mktemp -d) && mkfifo -- "$fifos/out" "$fifos/err" || die "cannot make the fifos of the output"
if [ -n "$review" ]; then
    # umask 077: the folders and the file are never wider than 700 and 600.
    (umask 077 && mkdir -p -- "${review_file%/*}" && rm -f -- "$review_file" &&
        printf '%s\n' "$review_token" > "$review_file") || die "cannot write $review_file"
fi

# tokens: each token, NUL-terminated, on stdout. printf is a builtin: a token is in no argv. No empty value:
# an empty pattern would match between each 2 bytes.
tokens() {
    printf '%s\0' "$token"
    [ -z "$review" ] || printf '%s\0' "$review_token"
}
# redact: copy stdin to stdout byte for byte, NUL bytes too, with each copy of a token replaced.
# A literal match by line (a token has no newline). The tokens go on fd 4, never on an argv.
# -I: no module from the work folder, where the step can write.
redact() {
    python3 -I -c '
import sys
tokens = [t for t in open(4, "rb").read().split(b"\0") if t]
for line in sys.stdin.buffer:
    for t in tokens:
        line = line.replace(t, b"<redacted>")
    sys.stdout.buffer.write(line)
    sys.stdout.buffer.flush()
' 4< <(tokens)
}

[ -z "$term" ] || exit 143
# bwrap reads its options in order: --args after --clearenv, or the token is cleared.
# printf is a builtin: the token is in no argv, not even of a child.
# <&0: an async command may get /dev/null for stdin, and the step reads its prompt there.
bwrap "${args[@]}" --args 3 -- "$@" 3< <(printf -- '--setenv\0CLAUDE_CODE_OAUTH_TOKEN\0%s\0' "$token") <&0 \
    > "$fifos/out" 2> "$fifos/err" &
pid=$!
redact < "$fifos/out" &
out_pid=$!
redact < "$fifos/err" >&2 &
err_pid=$!
[ -n "$term" ] || wait "$pid"
code=$?
if [ -n "$term" ]; then
    kill -TERM "$pid" 2>/dev/null
    wait "$pid"
    # The race: a TERM after the `exit 143` check and before the first `wait "$pid"` starts skips that wait, and $?
    # is then the 0 of `[ -n "$term" ]`: with no code=143, a TERM would exit 0. No test can hit a window this short.
    code=143
fi
# The race: a TERM kills the step after its fork and before it opens a fifo to write, so the filter of that fifo
# would wait in its open for ever; a writer that opens and closes ends it. The window is too short; no test can hit it.
: 5<>"$fifos/out" 6<>"$fifos/err"
wait "$out_pid" "$err_pid"
exit "$code"
