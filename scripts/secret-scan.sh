#!/usr/bin/env bash
# secret-scan.sh - scan a git repo for secrets and private names.
#
# Usage: scripts/secret-scan.sh [repo-dir]        (default: the current folder)
#
# Scans 4 places:
#   1. the working tree (tracked files and untracked files that are not ignored)
#   2. every blob of every commit of `git rev-list --all`
#   3. every commit message (the author and committer fields are not scanned)
#   4. the name of every tag of refs/tags, and the message of every annotated
#      tag, inner tags of a tag of a tag included (the tagger field and a
#      signature block are not scanned; a light tag has no message of its own),
#      and the blob, or every blob of the tree, that a tag points at straight
# with 2 lists:
#   - the generic patterns of scripts/patterns.txt (shipped in the repo)
#   - the private words of the local file named by env WORKBENCH_DENYLIST
#     (kept outside the repo; see scripts/denylist.example.txt)
#
# Output: 1 line for each hit, `<commit>:<path>:<line> <class>`. The commit is
# WORKTREE for the working tree; the path is COMMIT_MSG for a commit message.
# A hit in the message of an annotated tag is `tag-object:<sha>:<line> <class>`,
# <sha> the first 12 characters of the tag object id (never the tag name, which
# can itself be the private text); the line counts from the first line of the
# message. A hit in a tag name is `TAG_NAMES:<line> <class>`, the line of the
# list of tag names: the sorted ref names, then the names written inside the
# tag objects (an inner tag of a chain has no ref, only that name).
# A private-word class is printed as private:<class>. The matched text is never
# printed. A hit in a blob that a tag points at straight (no commit holds it)
# is `tag-object:<sha>:BLOB:<blob>:<line> <class>`, or `...:TREE:<blob>:...`
# for a blob of a tagged tree (`tag-target:<sha>` for a light tag); each id is
# its first 12 characters.
#
# Exit: 0 clean, 1 at least 1 hit, 2 WORKBENCH_DENYLIST names a missing file,
#       3 any other error (not a git repo, a bad pattern).
#
# Portable to bash 3.2 (macOS): no associative arrays, grep -E only (no -P).

set -u
export LC_ALL=C

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
patterns="${WORKBENCH_PATTERNS:-$here/patterns.txt}"
repo="${1:-.}"

die() { echo "secret-scan: $2" >&2; exit "$1"; }
abspath() {  # an absolute path for a file named relative to the caller's folder
    case "$1" in /*|[A-Za-z]:[\\/]*) printf '%s\n' "$1" ;; *) printf '%s/%s\n' "$PWD" "$1" ;; esac
}

[ -f "$patterns" ] || die 3 "pattern file not found: $patterns"
patterns="$(abspath "$patterns")"
denylist="${WORKBENCH_DENYLIST:-}"
[ -n "$denylist" ] && denylist="$(abspath "$denylist")"
cd "$repo" 2>/dev/null || die 3 "cannot enter $repo"
git rev-parse --git-dir >/dev/null 2>&1 || die 3 "not a git repo: $repo"

tmp="$(mktemp -d)" || die 3 "mktemp failed"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/re" "$tmp/fx" "$tmp/wt" "$tmp/blob" "$tmp/msg" "$tmp/clean"
: > "$tmp/allow"
: > "$tmp/hits"

# --- load the generic patterns -------------------------------------------------
while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in ''|'#'*) continue ;; esac
    cls="${line%% *}"
    re="${line#* }"
    [ "$cls" = "$line" ] && die 3 "bad pattern line (no class): $cls"
    if [ "$cls" = allow ]; then
        printf '%s\n' "$re" >> "$tmp/allow"
    else
        printf '%s\n' "$re" >> "$tmp/re/$cls"
    fi
done < "$patterns"
set -- "$tmp"/re/*
[ -e "$1" ] || die 3 "no pattern class in $patterns"

# --- load the private words ----------------------------------------------------
if [ -n "$denylist" ]; then
    [ -f "$denylist" ] || die 2 "WORKBENCH_DENYLIST names a missing file"
    ln=0
    while IFS= read -r line || [ -n "$line" ]; do
        ln=$((ln + 1))
        line="${line%$'\r'}"
        # trim the white space at both ends; a tab separates like a space
        line="${line#"${line%%[![:space:]]*}"}"
        line="${line%"${line##*[![:space:]]}"}"
        case "$line" in ''|'#'*) continue ;; esac
        cls="${line%%[[:space:]]*}"
        word="${line#"$cls"}"
        word="${word#"${word%%[![:space:]]*}"}"
        # the line number only: the content of the list is never printed
        [ -n "$word" ] || die 3 "WORKBENCH_DENYLIST line $ln: want <class> <word>"
        case "$cls" in *[!A-Za-z0-9_-]*) die 3 "WORKBENCH_DENYLIST line $ln: bad class name" ;; esac
        printf '%s\n' "$word" >> "$tmp/fx/$cls"
    done < "$denylist"
    # Each word becomes a whole-word ERE: metacharacters escaped, a non-word
    # character or a line end on each side. (grep -F -i aborts on some GNU
    # grep builds of Git for Windows, so the scan uses -E -i.)
    for c in "$tmp"/fx/*; do
        [ -e "$c" ] || continue
        sed -e 's/[][\.*^$+?(){}|]/\\&/g' \
            -e 's/^/(^|[^A-Za-z0-9_])/' -e 's/$/([^A-Za-z0-9_]|$)/' "$c" > "$c.re" &&
            mv "$c.re" "$c" || die 3 "cannot build the private-word patterns"
    done
else
    echo "secret-scan: WORKBENCH_DENYLIST is not set; private words are not scanned" >&2
fi

# --- collect the content: 1 file for each item, and a map id -> label ---------
: > "$tmp/map"
n=0

# 1. working tree
while IFS= read -r -d '' path; do
    [ -f "$path" ] || continue
    n=$((n + 1))
    cp -- "$path" "$tmp/wt/$n" || die 3 "cannot read $path"
    printf '%s\tWORKTREE:%s\n' "$n" "$path" >> "$tmp/map"
done < <(git ls-files -z -c -o --exclude-standard)

# 2. every blob of history, each blob once (label: a commit that holds it)
git rev-list --all > "$tmp/commits" 2>/dev/null || die 3 "git rev-list failed"
if [ -s "$tmp/commits" ]; then
    while IFS= read -r c; do
        git ls-tree -r --full-tree "$c" | awk -F'\t' -v c="$c" '{
            split($1, f, " "); if (f[2] == "blob") print f[3] "\t" c ":" $2 }'
    done < "$tmp/commits" | awk -F'\t' '!seen[$1]++' > "$tmp/blobs"
    while IFS="$(printf '\t')" read -r sha label; do
        n=$((n + 1))
        git cat-file blob "$sha" > "$tmp/blob/$n" || die 3 "cannot read blob"
        printf '%s\t%s\n' "$n" "$label" >> "$tmp/map"
    done < "$tmp/blobs"
    # 3. commit messages (the body only: %B; no author or committer field)
    while IFS= read -r c; do
        n=$((n + 1))
        git log -1 --format=%B "$c" > "$tmp/msg/$n"
        printf '%s\t%s:COMMIT_MSG\n' "$n" "$c" >> "$tmp/map"
    done < "$tmp/commits"
fi

# 4. annotated tag messages: the tag object minus its header (object, type,
#    tag, tagger), which ends at the first empty line, and minus a trailing
#    PGP or SSH signature block. A tag of a tag is followed down the chain,
#    so an inner tag object that only the outer one reaches is read too.
git for-each-ref refs/tags --format='%(objecttype) %(objectname) %(refname)' \
    > "$tmp/tags" 2>/dev/null || die 3 "git for-each-ref failed"
while read -r type sha ref; do
    tagobj=""
    while [ "$type" = tag ]; do  # a light tag points at a commit: no message
        n=$((n + 1))
        tagobj="$sha"
        git cat-file tag "$sha" > "$tmp/tagobj" || die 3 "cannot read a tag object"
        # the signature is cut only when it is the trailing block: its END
        # line is the last line that is not empty, and its BEGIN line of the
        # same kind is the last one before it; text after it stays scanned
        awk 'body { b[++k] = $0; next } /^$/ { body = 1 }
             END {
                 cut = k + 1; last = k
                 while (last > 0 && b[last] == "") last--
                 if (last > 0 && b[last] ~ /^-----END (PGP|SSH) SIGNATURE-----$/) {
                     kind = substr(b[last], 10, 3)
                     for (i = last - 1; i > 0; i--)
                         if (b[i] == "-----BEGIN " kind " SIGNATURE-----") { cut = i; break }
                 }
                 for (i = 1; i < cut; i++) print b[i]
             }' "$tmp/tagobj" > "$tmp/msg/$n"
        # the header: the object this tag points at, its type, the tag's name
        # (the name goes to the TAG_NAMES item, never into a label)
        read -r type sha < <(awk '/^$/ { exit }
            $1 == "object" { o = $2 } $1 == "type" { t = $2 }
            $1 == "tag" { sub(/^tag /, ""); print >> names }
            END { print t, o }' names="$tmp/ownnames" "$tmp/tagobj")
        # the label is the tag object id, outer and inner alike: a tag name
        # holds free text and can itself be the private word
        printf '%s\ttag-object:%.12s\n' "$n" "$tagobj" >> "$tmp/map"
    done
    # 5. a tag (light or annotated) can point straight at a blob or a tree,
    #    which no commit holds: scan that blob, or every blob of that tree.
    #    The label is the last tag object id (or the target id for a light
    #    tag) plus the blob id; never a tag name, never a path of the tree.
    case "$type" in
        blob) printf '%s\n' "$sha" ;;
        tree) git ls-tree -r "$sha" | awk -F'\t' '{
                  split($1, f, " "); if (f[2] == "blob") print f[3] }' ;;
        *) continue ;;
    esac > "$tmp/tagblobs" || die 3 "cannot list a tagged tree"
    if [ -n "$tagobj" ]; then via="tag-object:$tagobj"; else via="tag-target:$sha"; fi
    kind="$(printf '%s' "$type" | tr a-z A-Z)"
    while IFS= read -r b; do
        n=$((n + 1))
        git cat-file blob "$b" > "$tmp/blob/$n" || die 3 "cannot read a tagged blob"
        printf '%s\t%.23s:%s:%.12s\n' "$n" "$via" "$kind" "$b" >> "$tmp/map"
    done < "$tmp/tagblobs"
done < "$tmp/tags"
# the tag names themselves (a hit prints the line number, not the name): the
# sorted ref names, then the names written in the tag objects (inner ones too)
n=$((n + 1))
{ sed 's|^[^ ]* [^ ]* refs/tags/||' "$tmp/tags"
  [ -f "$tmp/ownnames" ] && cat "$tmp/ownnames"; } > "$tmp/msg/$n"
printf '%s\tTAG_NAMES\n' "$n" >> "$tmp/map"

shopt -s nullglob
files=("$tmp"/wt/* "$tmp"/blob/* "$tmp"/msg/*)
[ "${#files[@]}" -eq 0 ] && exit 0

# Replace each allowed placeholder by a space (line numbers stay the same),
# in 1 awk run that writes a clean copy of each item to $tmp/clean/<id>.
# (awk, not sed -i: the -i flag differs between GNU and BSD sed.)
awk -v allowfile="$tmp/allow" -v out="$tmp/clean" '
    BEGIN { while ((getline r < allowfile) > 0) if (r != "") re[++na] = r }
    FNR == 1 { if (dst != "") close(dst); k = split(FILENAME, p, "/"); dst = out "/" p[k]; printf "" > dst }
    { for (i = 1; i <= na; i++) gsub(re[i], " "); print > dst }
' "${files[@]}" || die 3 "bad allow pattern"
files=("$tmp"/clean/*)

# --- scan: 1 grep for each class over all content -----------------------------
report() {  # $1 class label; stdin: grep -H -n output. Prints labels only.
    awk -v cls="$1" '
        NR == FNR { i = index($0, "\t"); m[substr($0, 1, i - 1)] = substr($0, i + 1); next }
        {
            i = index($0, ":"); f = substr($0, 1, i - 1); rest = substr($0, i + 1)
            j = index(rest, ":"); ln = substr(rest, 1, j - 1)
            k = split(f, p, "/")
            print m[p[k]] ":" ln " " cls
        }' "$tmp/map" -
}

for c in "$tmp"/re/*; do
    cls="$(basename "$c")"
    grep -a -H -n -E -f "$c" -- "${files[@]}" > "$tmp/out"
    rc=$?
    [ "$rc" -gt 1 ] && die 3 "grep failed for class $cls (bad regex?)"
    report "$cls" < "$tmp/out" >> "$tmp/hits"
done
for c in "$tmp"/fx/*; do
    cls="$(basename "$c")"
    grep -a -H -n -i -E -f "$c" -- "${files[@]}" > "$tmp/out"
    rc=$?
    [ "$rc" -gt 1 ] && die 3 "grep failed for private class $cls"
    report "private:$cls" < "$tmp/out" >> "$tmp/hits"
done
rm -f "$tmp/out"

if [ -s "$tmp/hits" ]; then
    sort -u "$tmp/hits"
    echo "secret-scan: $(sort -u "$tmp/hits" | wc -l | tr -d ' ') hit(s)" >&2
    exit 1
fi
echo "secret-scan: clean" >&2
exit 0
