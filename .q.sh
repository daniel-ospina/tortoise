#!/usr/bin/env bash
# read-only helper: list open pull requests mentioning an issue number
for N in "$@"; do
  echo "=== #$N ==="
  gh api "search/issues?q=repo:daniel-ospina/tortoise+is:open+is:pr+${N}+in:body&per_page=20" \
    --jq '.items[]? | "\(.number) \(.state) \(.title)"' 2>/dev/null
  gh api "search/issues?q=repo:daniel-ospina/tortoise+is:open+is:pr+${N}+in:title&per_page=20" \
    --jq '.items[]? | "TITLE-MATCH \(.number) \(.state) \(.title)"' 2>/dev/null
done
