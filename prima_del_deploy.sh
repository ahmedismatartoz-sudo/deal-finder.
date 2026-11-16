#!/usr/bin/env bash
# Controlli da fare PRIMA di mettere online (vedi REGOLE_DEPLOY.md). Non fa deploy, non tocca Render.
#   ./prima_del_deploy.sh            impronta delle regole + compileall + import + test nei due modi
# Codice di uscita diverso da 0 = NON mettere online.
set -euo pipefail
cd "$(dirname "$0")"
PY="${PYTHON:-python3}"

echo "== ramo e stato"
git rev-parse --abbrev-ref HEAD
if [ -n "$(git status --porcelain)" ]; then
  echo "ATTENZIONE: modifiche non committate (il deploy prende solo quello che è su GitHub)"
fi

echo "== orario (finestre di deploy 13:00 e 20:00, ora di Roma)"
ora=$(TZ=Europe/Rome date +%H)
if [ "$ora" != "13" ] && [ "$ora" != "20" ]; then
  echo "ATTENZIONE: fuori dalle finestre 13:00 e 20:00 (ora di Roma: ${ora}). Solo correzioni urgenti, vedi REGOLE_DEPLOY.md"
fi

"$PY" run_tests.py --deploy     # passo 0: impronta delle regole; poi compileall, import, test nei due modi
echo "PRIMA DEL DEPLOY: tutto ok. Dopo la messa online: python -m dealfinder.canarino entro 15 minuti."
