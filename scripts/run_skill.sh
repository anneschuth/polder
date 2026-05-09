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
# Env-vars:
#   POLDER_CLAUDE_MODEL  modelnaam voor `claude --model`. Default
#                        claude-haiku-4-5. Aanroepers (parse_*_local.sh) kunnen
#                        zelf een ander default zetten als de skill een sterker
#                        model nodig heeft (bv. parse-organogram met Opus).
#
# Exit-codes:
#   0   succes
#   99  rate-limit gedetecteerd in claude-output. Output-file wordt NIET
#       geschreven (anders zou rate-limit-tekst als JSON gestaged worden).
#       Aanroepers in src/polder/ingest.py interpreteren 99 als signaal om
#       de hele pipeline-fase af te breken.
#   anders  gewone fout (skill niet gevonden, claude crash, etc.)
#
# Compatibel met bash 3.2 (macOS) en bash 5+ (Linux).

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

# Default model: Haiku 4.5 — een orde van grootte goedkoper dan Sonnet 4.6 en
# voor de parse/resolve-skills vrijwel even accuraat. Aanroepers die een sterker
# model nodig hebben (bv. parse_organogram_local.sh voor PDF-vision) zetten
# POLDER_CLAUDE_MODEL voor de aanroep.
CLAUDE_MODEL="${POLDER_CLAUDE_MODEL:-claude-haiku-4-5}"

# Bouw prompt-prefix. De input gaat na de prefix als stdin of als string.
PROMPT_PREFIX="Gebruik de skill \`${SKILL_NAME}\` zoals beschreven in \`.claude/skills/${SKILL_NAME}/SKILL.md\`. Volg de skill-instructies exact. Lees ook \`.claude/skills/${SKILL_NAME}/SKILL.md\` zelf voordat je begint. Input volgt hieronder."

# Detect rate-limit markers in claude-output. Markers gebaseerd op observed
# error-strings: "Claude AI usage limit reached", "hit your limit",
# "rate limit", "rate-limit", "exceeded".
is_rate_limited() {
  # $1 = pad naar output-bestand om te scannen
  if [ ! -f "$1" ]; then
    return 1
  fi
  if grep -qiE "hit your (usage |rate )?limit|rate[ -]limit|rate limited|usage limit reached|exceeded.*limit|429" "$1"; then
    return 0
  fi
  return 1
}

run_claude() {
  # $1 = full prompt text om te pipen via stdin
  # $2 = pad waar stdout naartoe moet (mag leeg zijn voor terminal-stdout)
  # We chdir naar REPO_ROOT zodat .claude/ resolveerbaar is.
  local prompt="$1"
  local out_file="$2"
  local tmp_out
  tmp_out="$(mktemp -t polder-run-skill.XXXXXX)"
  # Schrijf stderr ook naar het temp-bestand zodat rate-limit-detectie zowel
  # over stdout als stderr scant; de claude-CLI print rate-limit-fouten soms
  # naar stderr.
  if (
    cd "$REPO_ROOT"
    printf '%s' "$prompt" | "$CLAUDE_BIN" -p \
      --model "$CLAUDE_MODEL" \
      --permission-mode bypassPermissions \
      >"$tmp_out" 2>>"$tmp_out"
  ); then
    local claude_rc=0
  else
    local claude_rc=$?
  fi

  if is_rate_limited "$tmp_out"; then
    echo "run_skill.sh: rate-limit gedetecteerd, output NIET geschreven naar ${out_file:-stdout}" >&2
    rm -f "$tmp_out"
    return 99
  fi

  if [ "$claude_rc" -ne 0 ]; then
    # Niet rate-limit, gewone fout. Print stderr/stdout naar onze stderr.
    cat "$tmp_out" >&2
    rm -f "$tmp_out"
    return "$claude_rc"
  fi

  if [ -n "$out_file" ]; then
    cp "$tmp_out" "$out_file"
  else
    cat "$tmp_out"
  fi
  rm -f "$tmp_out"
  return 0
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
  if run_claude "$FULL_PROMPT" "$OUTPUT"; then
    echo "run_skill.sh: output geschreven naar $OUTPUT (model=$CLAUDE_MODEL)" >&2
  else
    rc=$?
    if [ "$rc" -eq 99 ]; then
      exit 99
    fi
    exit "$rc"
  fi
else
  if ! run_claude "$FULL_PROMPT" ""; then
    rc=$?
    if [ "$rc" -eq 99 ]; then
      exit 99
    fi
    exit "$rc"
  fi
fi
