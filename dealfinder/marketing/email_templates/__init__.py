"""24/11 (cliente): i due modelli HTML dell'email "come il WhatsApp", pronti per Brevo.

con_esempi.html     testo del template WhatsApp skovo_due_auto_foto: foto della prima auto in alto, due auto
                    ({{ contact.AUTO1 }}, {{ contact.DATI1 }}, {{ contact.FOTO1 }}, {{ contact.AUTO2 }}, {{ contact.DATI2 }})
senza_esempi.html   testo del template WhatsApp skovo_servizio (solo testo)
In tutti e due i tre pulsanti del WhatsApp: "Mi interessa" (https://{{ contact.MI_LINK }}), "Chiamami" (tel: del
titolare), "Non mi interessa" (https://{{ contact.STOP_LINK }}: pagina di conferma, poi disiscrive), e in fondo il link
di disiscrizione di Brevo ({{ unsubscribe }}). MI_LINK e STOP_LINK sono salvati SENZA protocollo e "https://" sta nel
modello: così Brevo traccia i clic anche sui link presi da un attributo. Per Outlook: padding sulla cella dei pulsanti,
tabella fantasma da 600 e mso-hide:all sul testo di anteprima. Gli attributi li scrive marketing/email_esempi.py. Qui non si crea e non si invia niente.
"""
from __future__ import annotations

from pathlib import Path

QUI = Path(__file__).resolve().parent
MITTENTE = {"name": "Skovo", "email": "info@skovo.net"}
MODELLI = {
    "con": {"file": "con_esempi.html", "lista": "Skovo email con esempi 24-11",
            "oggetto": "Due auto di privati sotto mercato, in linea con voi"},
    "senza": {"file": "senza_esempi.html", "lista": "Skovo email senza esempi 24-11",
              "oggetto": "Auto di privati sotto il prezzo di mercato, vicino a voi"},
}


def html(versione: str) -> str:
    """L'HTML del modello "con" o "senza", da incollare in una campagna Brevo."""
    return (QUI / MODELLI[versione]["file"]).read_text(encoding="utf-8")
