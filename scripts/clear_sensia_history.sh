#!/usr/bin/env bash
#
# Borra el historial de uno o más números en el agente Sensia:
#   - mensajes de chat (ChatHistory)
#   - memoria / checkpoint de LangGraph (thread_id = número)
#
# Uso:
#   ./scripts/clear_sensia_history.sh 59176616013 59175640698 59170088388
#
# Requiere el CLI de Railway linkeado al proyecto: inyecta ADMIN_API_KEY del
# servicio Sensia sin imprimirlo. Responde {"messages_deleted": N} por número
# (0 = no tenía historial; igual limpia el checkpoint, no falla).
#
set -euo pipefail

BASE="${SENSIA_BASE_URL:-https://sensia-production.up.railway.app}"

if [ "$#" -lt 1 ]; then
  echo "Uso: $0 <numero> [numero...]" >&2
  exit 1
fi
NUMS="$*"

railway run --service Sensia --environment production -- bash -c '
  BASE="'"$BASE"'"
  for n in '"$NUMS"'; do
    echo "== $n =="
    curl -s -X DELETE -H "X-Admin-Key: $ADMIN_API_KEY" \
      "$BASE/api/v1/admin/tenants/sensia/conversations/$n"
    echo
  done
'
