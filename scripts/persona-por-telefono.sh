#!/usr/bin/env bash
# Consulta la info de una persona (cliente) en el CRM de Kohlberg por su teléfono.
# Companion de /api/pedidos/por-telefono: matchea por sufijo de dígitos, así que
# sortea el split de person_id duplicados. Uso para depurar/ver datos del cliente.
#
#   export CRM_TOKEN='...'        # API key del CRM (Configuración > Usuarios > API key)
#   scripts/persona-por-telefono.sh "+591 7661-6013"
set -euo pipefail

CRM_URL="${CRM_URL:-https://kohlberg.sofopolis.com}"

if [[ -z "${CRM_TOKEN:-}" ]]; then
    echo "Falta CRM_TOKEN. Generá uno en el CRM (Configuración > Usuarios > API key) y exportalo:" >&2
    echo "  export CRM_TOKEN='...'" >&2
    exit 1
fi

if [[ $# -eq 0 ]]; then
    echo "Uso: $0 <telefono>" >&2
    exit 1
fi

# Todo lo que no sea dígito se descarta acá también: así "+591 7661-6013",
# "59176616013" y "76616013" son la misma llamada y el servidor no tiene que
# adivinar qué separadores mandó la terminal.
TELEFONO="$(printf '%s' "$*" | tr -cd '0-9')"

curl -sS --get "${CRM_URL}/api/personas/por-telefono" \
    --data-urlencode "telefono=${TELEFONO}" \
    -H "Authorization: Bearer ${CRM_TOKEN}" \
    -H 'Accept: application/json' \
    -w '\n' \
| { command -v jq >/dev/null 2>&1 && jq . || cat; }
