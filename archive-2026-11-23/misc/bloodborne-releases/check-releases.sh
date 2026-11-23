#!/usr/bin/env bash
# Linux port of check-releases.ps1: look for a new release of the two Bloodborne PC port repos and
# raise a desktop notification when the tag changed. Read-only against GitHub (needs `gh auth login`).
# State lives in ~/.local/state/bbport-releases.json. Run by bloodborne-release-check.timer.
set -u
repos=("deadinside28/bloodborne_pc" "Supermedo/bloodborne_pc")
state="${XDG_STATE_HOME:-$HOME/.local/state}/bbport-releases.json"
mkdir -p "$(dirname "$state")"
[ -s "$state" ] || echo '{}' > "$state"

news=()
for repo in "${repos[@]}"; do
    latest="$(gh release view --repo "$repo" --json tagName,name --jq '[.tagName,.name]|@tsv' 2>/dev/null)" || continue
    [ -n "$latest" ] || continue
    tag="${latest%%$'\t'*}"
    name="${latest#*$'\t'}"
    old="$(jq -r --arg r "$repo" '.[$r] // empty' "$state")"
    if [ "$old" != "$tag" ]; then
        [ -n "$old" ] && news+=("$name ($repo)")
        jq --arg r "$repo" --arg t "$tag" '.[$r]=$t' "$state" > "$state.tmp" && mv "$state.tmp" "$state"
    fi
done

if [ "${#news[@]}" -gt 0 ]; then
    body="$(printf '%s\n' "${news[@]}")"
    if command -v notify-send >/dev/null; then
        notify-send -t 15000 'New Bloodborne port release' "$body"
    fi
    printf '%s\n' "$body"
fi
