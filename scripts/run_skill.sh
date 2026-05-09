#!/usr/bin/env bash
# run_skill.sh - generieke runner voor een Claude Code skill via `claude -p`.
#
# Gebruik:
#   ./scripts/run_skill.sh <skill-name> <prompt-file-or-text> [output-file]
#
# Argumenten:
#   skill-name           naam van een skill onder .claude/skills/<name>/
#   prompt-file-or-text  pad naar een bestand met de input (XML, JSON, PDF-pad,
#                        of vrije tekst). Wordt op stdin gepipet als bestand.
#                        Als de string geen bestaand pad is, wordt hij als
#                        letterlijke tekst meegegeven.
#   output-file          optioneel. Schrijft stdout van claude -p naar dit pad.
#                        Default: stdout van dit script.
#
# Exit non-zero bij failure. Compatibel met bash 3.2 (macOS) en bash 5+ (Linux).

set -euo pipefail

if [ "$#" -lt 2 ] || [ "$#" -gt 3 ]; then
  echo "usage: $0 <skill-name> <prompt-file-or-text> [output-file]" >&2
  exit 2
fi

SKILL_NAME="$1"
INPUT="$2"
OUTPUT="${3:-}"

# Vind de repo-root door dit script-pad te resolven.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SKILL_PATH="$REPO_ROOT/.claude/skills/$SKILL_NAME/SKILL.md"
if [ ! -f "$SKILL_PATH" ]; then
  echo "run_skill.sh: skill '$SKILL_NAME' niet gevonden op $SKILL_PATH" >&2
  exit 1
fi

CLAUDE_BIN="${CLAUDE_BIN:-claude}"
if ! command -v "$CLAUDE_BIN" >/dev/null 2>&1; then
  if [ -x "/Users/anneschuth/.local/bin/claude" ]; then
    CLAUDE_BIN="/Users/anneschuth/.local/bin/claude"
  else
    echo "run_skill.sh: claude CLI niet gevonden in PATH (CLAUDE_BIN=$CLAUDE_BIN)" >&2
    exit 1
  fi
fi

# Bouw prompt-prefix. De input gaat na de prefix als stdin of als string.
PROMPT_PREFIX="Gebruik de skill \`${SKILL_NAME}\` zoals beschreven in \`.claude/skills/${SKILL_NAME}/SKILL.md\`. Volg de skill-instructies exact. Lees ook \`.claude/skills/${SKILL_NAME}/SKILL.md\` zelf voordat je begint. Input volgt hieronder."

run_claude() {
  # $1 = full prompt text om te pipen via stdin
  # We chdir naar REPO_ROOT zodat .claude/ resolveerbaar is.
  (
    cd "$REPO_ROOT"
    printf '%s' "$1" | "$CLAUDE_BIN" -p --permission-mode bypassPermissions
  )
}

# Bouw de full prompt: prefix + input.
if [ -f "$INPUT" ]; then
  # Input is een bestaand bestand. Lees inhoud en voeg toe aan prompt.
  # Voor binaire bestanden (PDF) geven we het pad door, niet de inhoud.
  case "$INPUT" in
    *.pdf|*.PDF|*.png|*.PNG|*.jpg|*.JPG|*.jpeg|*.JPEG)
      ABS_INPUT="$(cd "$(dirname "$INPUT")" && pwd)/$(basename "$INPUT")"
      FULL_PROMPT="${PROMPT_PREFIX}

Pad naar input-bestand: ${ABS_INPUT}

Lees dit bestand met de Read-tool en verwerk het volgens de skill."
      ;;
    *)
      FILE_CONTENT="$(cat "$INPUT")"
      FULL_PROMPT="${PROMPT_PREFIX}

\`\`\`
${FILE_CONTENT}
\`\`\`"
      ;;
  esac
else
  # Input is een tekst-string.
  FULL_PROMPT="${PROMPT_PREFIX}

${INPUT}"
fi

if [ -n "$OUTPUT" ]; then
  OUTPUT_DIR="$(dirname "$OUTPUT")"
  mkdir -p "$OUTPUT_DIR"
  run_claude "$FULL_PROMPT" >"$OUTPUT"
  echo "run_skill.sh: output geschreven naar $OUTPUT" >&2
else
  run_claude "$FULL_PROMPT"
fi
