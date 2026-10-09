#!/usr/bin/env bash
# Scarica le foto degli annunci nella cartella img/ (gira durante il build del sito su Render)
cd "$(dirname "$0")"
mkdir -p img
ok=0; ko=0
while read -r f u1 u2; do
  [ -z "$f" ] && continue
  for u in "$u1" "$u2"; do
    code=$(curl -sL -A "Mozilla/5.0" -H "Referer: https://www.subito.it/" -o "img/$f" -w "%{http_code}" --max-time 20 "$u")
    type=$(file -b --mime-type "img/$f" 2>/dev/null || echo "?")
    if [ "$code" = "200" ] && [ -s "img/$f" ] && [[ "$type" == image/* || "$type" == "?" ]]; then ok=$((ok+1)); echo "FOTO_OK $f $code $type"; continue 2; fi
    echo "FOTO_KO $f $code $type $u"
  done
  rm -f "img/$f"; ko=$((ko+1))
done < foto.txt
echo "FOTO_TOTALE scaricate=$ok mancanti=$ko"
