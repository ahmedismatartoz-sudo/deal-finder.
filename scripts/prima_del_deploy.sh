#!/usr/bin/env bash
# Controllo prima della messa online (3/11, piano di ricostruzione): compileall, import dei moduli principali e test
# nei due modi (default del codice e regole di produzione), tutto con `python run_tests.py --deploy`.
# Stampa PASS o FAIL; codice di uscita 0 solo se tutto è passato. Nessun database, nessuna rete, nessun segreto.
#
#     scripts/prima_del_deploy.sh            (da qualunque cartella)
#     PYTHON=python3.13 scripts/prima_del_deploy.sh
set -u
cd "$(dirname "$0")/.." || { echo "FAIL (cartella del progetto non trovata)"; exit 2; }
PY="${PYTHON:-python}"
log="$(mktemp "${TMPDIR:-/tmp}/prima_del_deploy.XXXXXX")"
"$PY" run_tests.py --deploy >"$log" 2>&1
codice=$?
grep -E '^(== |FAIL|  FALLITO|DEPLOY|\[)' "$log"
if [ "$codice" -eq 0 ] && grep -q '^DEPLOY OK' "$log"; then
    echo "PASS"
    rm -f "$log"
    exit 0
fi
echo "FAIL (codice $codice, uscita completa in $log)"
exit 1
