#!/usr/bin/env bash
# lint_text.sh — scant alle .md files op AI-tells en banned phrases.
#
# Exit 0: schoon. Exit 1: één of meer issues gevonden.
#
# Uitsluitingen: .venv/, _cache/, node_modules/, .git/, en LICENSE*.
#
# Compatibel met bash 3.2 (macOS systeembash).

set -uo pipefail

ROOT="${1:-.}"

# Em-dash: U+2014. Letterlijke match.
EMDASH=$(printf '\xE2\x80\x94')

# Banned phrases EN, case-insensitive, regex-alternation.
BANNED_EN='it is worth noting|in the current landscape|delve|deep dive|tapestry|groundbreaking|game-changer|leverage|load-bearing|navigate the|valuable contribution'

# Banned phrases NL, case-insensitive.
# verkennen/ontrafelen kunnen legitiem zijn; we accepteren ze als false positive.
BANNED_NL='Het is belangrijk om op te merken|In het huidige landschap|Laten we eerlijk zijn|deep dive|game-changer|fundamenteel|cruciaal|baanbrekend|revolutionair|Kortom,'

ISSUES=0
COUNT=0

# bash 3.2 heeft geen mapfile; loop direct over find-output.
while IFS= read -r f; do
  COUNT=$((COUNT + 1))

  # Em-dashes
  while IFS= read -r lineno; do
    [ -z "$lineno" ] && continue
    echo "em-dash: $f:$lineno"
    ISSUES=$((ISSUES + 1))
  done < <(grep -n -F "$EMDASH" "$f" 2>/dev/null | cut -d: -f1)

  # Banned EN
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    echo "banned-en: $f:$hit"
    ISSUES=$((ISSUES + 1))
  done < <(grep -n -i -E "$BANNED_EN" "$f" 2>/dev/null)

  # Banned NL
  while IFS= read -r hit; do
    [ -z "$hit" ] && continue
    echo "banned-nl: $f:$hit"
    ISSUES=$((ISSUES + 1))
  done < <(grep -n -i -E "$BANNED_NL" "$f" 2>/dev/null)
done < <(
  find "$ROOT" -type f -name '*.md' \
    -not -path '*/.venv/*' \
    -not -path '*/_cache/*' \
    -not -path '*/node_modules/*' \
    -not -path '*/.git/*' \
    -not -name 'LICENSE*' \
    | sort
)

if [ "$COUNT" -eq 0 ]; then
  echo "lint_text.sh: geen .md bestanden gevonden onder $ROOT"
  exit 0
fi

if [ "$ISSUES" -eq 0 ]; then
  echo "0 issues"
  exit 0
fi

echo "$ISSUES issues found"
exit 1
