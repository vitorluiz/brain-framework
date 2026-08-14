#!/bin/bash
# ===================================================================
# Brain Backup & Restore — Ecossistema Granjimmy
# ===================================================================
# Uso:
#   bash backup.sh                     # backup de TODOS os brains
#   bash backup.sh --global            # backup apenas do brain global
#   bash backup.sh --expert jimmy      # backup apenas do brain do jimmy
#   bash backup.sh --expert gtic       # backup apenas do brain do gtic
#
#   bash backup.sh restore --global <timestamp>
#   bash backup.sh restore --expert jimmy <timestamp>
#
# ===================================================================

# Caminhos absolutos — não dependem de cd ou PWD
BRAIN_ROOT="/home/hermes/.hermes/brain"
BACKUP_DIR="$BRAIN_ROOT/backup"
GLOBAL_DIR="$BRAIN_ROOT/global"
EXPERTS_DIR="$BRAIN_ROOT/experts"

TIMESTAMP=$(date +%Y%m%d_%H%M%S)
PROFILE_BACKUP_DAYS=7

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"; }

rotate_backups() {
  local dir="$1"
  if [ -d "$dir" ]; then
    find "$dir" -name "*.db" -mtime +$PROFILE_BACKUP_DAYS -delete 2>/dev/null || true
  fi
}

backup_brain() {
  local label="$1"
  local src_dir="$2"
  local dst_dir="$BACKUP_DIR/$label"

  mkdir -p "$dst_dir"
  if [ ! -d "$src_dir" ]; then
    log "⚠ N/A — diretório não encontrado: $src_dir"
    return 0
  fi
  if [ ! -f "$src_dir/brain.db" ]; then
    log "⚠ N/A — brain.db não encontrado em $src_dir"
    return 0
  fi

  cp "$src_dir/brain.db" "$dst_dir/brain_${TIMESTAMP}.db"
  local size=$(stat -c%s "$src_dir/brain.db" 2>/dev/null || echo "?")
  log "✅ backup $label: $(stat -c%s "$dst_dir/brain_${TIMESTAMP}.db") bytes (origem: $size bytes)"
  rotate_backups "$dst_dir"
  return 0
}

do_backup() {
  case "${1:-}" in
    --global)
      backup_brain "global" "$GLOBAL_DIR"
      ;;
    --expert)
      local expert="${2:-}"
      if [ -z "$expert" ]; then
        echo "❌ Uso: bash backup.sh --expert <profile>"
        echo "   Profiles: $(ls "$EXPERTS_DIR" 2>/dev/null | tr '\n' ' ')"
        exit 1
      fi
      backup_brain "expert_$expert" "$EXPERTS_DIR/$expert"
      ;;
    "")
      log "📦 BACKUP GLOBAL (todos os brains)"
      local failed=0
      backup_brain "global" "$GLOBAL_DIR" || failed=1
      for exp in "$EXPERTS_DIR"/*/; do
        [ -d "$exp" ] || continue
        local name=$(basename "$exp")
        backup_brain "expert_$name" "$EXPERTS_DIR/$name" || failed=1
      done
      if [ $failed -eq 0 ]; then
        log "📦 BACKUP CONCLUÍDO — $TIMESTAMP"
      else
        log "⚠ BACKUP FINALIZADO COM ADVERTÊNCIAS — $TIMESTAMP"
      fi
      ;;
    *)
      echo "❌ Opção desconhecida: $1"
      echo "   Use: bash backup.sh [--global|--expert <profile>]"
      exit 1
      ;;
  esac
}

do_restore() {
  local label="$1"
  local ts="$2"
  local dst_dir=""

  case "$label" in
    global)      dst_dir="$GLOBAL_DIR" ;;
    expert_*)    dst_dir="$EXPERTS_DIR/${label#expert_}" ;;
    *)           echo "❌ Label inválido: $label"; exit 1 ;;
  esac

  local src="$BACKUP_DIR/$label/brain_${ts}.db"
  if [ ! -f "$src" ]; then
    echo "❌ Backup não encontrado: $src"
    echo "   Disponíveis em $BACKUP_DIR/$label/:"
    ls "$BACKUP_DIR/$label/" 2>/dev/null | grep "brain_.*\.db" | sed 's/.*brain_//; s/\.db//' | tr '\n' ' '
    exit 1
  fi
  if [ ! -d "$dst_dir" ]; then
    echo "❌ Destino inexistente: $dst_dir"
    exit 1
  fi

  log "🔄 Restaurando $label ← $ts..."
  cp "$src" "$dst_dir/brain.db"
  log "✅ Restaurado: $label"
}

# ── Main ──

if [ "${1:-}" = "restore" ]; then
  shift
  if [ "${1:-}" = "--global" ]; then
    do_restore "global" "${2:-}"
  elif [ "${1:-}" = "--expert" ]; then
    do_restore "expert_${2:-}" "${3:-}"
  else
    echo "❌ Uso: bash backup.sh restore --global <ts> | --expert <profile> <ts>"
    exit 1
  fi
  exit 0
fi

do_backup "$@"
