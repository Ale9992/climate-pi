#!/usr/bin/env bash
# ============================================================================
# setup.sh — Installazione di Climate Automation.
#
# Esegue:
#   1. crea/aggiorna il virtualenv Python e installa requirements.txt
#   2. builda la dashboard React (se Node.js e' presente)
#   3. installa e abilita il servizio systemd (richiede sudo)
#
# Pensato per Raspberry Pi OS Lite / Debian. Va eseguito DOPO il mapping tool,
# quando config/config.yaml e' gia' presente.
#
# Uso:   ./setup.sh            (installazione completa)
#        ./setup.sh --no-service   (salta l'installazione systemd)
# ============================================================================
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_USER="$(whoami)"
PY="${PROJECT_DIR}/.venv/bin/python"
INSTALL_SERVICE=1
[ "${1:-}" = "--no-service" ] && INSTALL_SERVICE=0

echo "==> Climate Automation setup"
echo "    Project: ${PROJECT_DIR}"
echo "    User   : ${RUN_USER}"

# --- 0. Sanity: config presente? -------------------------------------------
if [ ! -f "${PROJECT_DIR}/config/config.yaml" ]; then
  echo "!! config/config.yaml non trovato."
  echo "   Esegui prima il mapping tool:  python tools/mapping_tool.py"
  exit 1
fi

# --- 1. Virtualenv + dipendenze Python -------------------------------------
echo "==> [1/3] Virtualenv e dipendenze Python"
if [ ! -d "${PROJECT_DIR}/.venv" ]; then
  python3 -m venv "${PROJECT_DIR}/.venv"
fi
"${PY}" -m pip install --upgrade pip >/dev/null
"${PY}" -m pip install -r "${PROJECT_DIR}/requirements.txt"
echo "    Dipendenze Python installate."

# --- 2. Build dashboard -----------------------------------------------------
echo "==> [2/3] Build dashboard"
if command -v npm >/dev/null 2>&1; then
  pushd "${PROJECT_DIR}/dashboard" >/dev/null
  npm install
  npm run build
  popd >/dev/null
  echo "    Dashboard buildata in dashboard/dist."
else
  echo "    Node.js/npm assente: salto la build della dashboard."
  echo "    (le API resteranno comunque attive su :8000)"
fi

# --- 3. Servizio systemd ----------------------------------------------------
if [ "${INSTALL_SERVICE}" -eq 1 ]; then
  echo "==> [3/3] Servizio systemd (richiede sudo)"
  SERVICE_SRC="${PROJECT_DIR}/climate-automation.service"
  SERVICE_DST="/etc/systemd/system/climate-automation.service"
  TMP="$(mktemp)"
  sed -e "s|__PROJECT_DIR__|${PROJECT_DIR}|g" \
      -e "s|__USER__|${RUN_USER}|g" \
      "${SERVICE_SRC}" > "${TMP}"
  sudo cp "${TMP}" "${SERVICE_DST}"
  rm -f "${TMP}"
  sudo systemctl daemon-reload
  sudo systemctl enable climate-automation.service
  sudo systemctl restart climate-automation.service
  echo "    Servizio installato e avviato."
  echo "    Stato:  sudo systemctl status climate-automation"
  echo "    Log  :  journalctl -u climate-automation -f"
else
  echo "==> [3/3] Servizio systemd: SALTATO (--no-service)"
  echo "    Avvio manuale:  ${PY} ${PROJECT_DIR}/main.py"
fi

echo "==> Fatto. Dashboard/API: http://$(hostname -I 2>/dev/null | awk '{print $1}'):8000"
