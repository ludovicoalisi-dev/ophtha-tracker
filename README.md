# ophtha-tracker

Monitoraggio delle scadenze (abstract, early bird, iscrizioni) dei principali
congressi oftalmologici internazionali e italiani, con un report automatico
pubblicato come **issue GitHub** il 1° e il 15 di ogni mese.

## Come funziona

1. `data/conferences.yaml` contiene i congressi: sito, sottospecialità e, per
   ogni edizione, sede, date e scadenze. Ogni dato ha il flag `verified`:
   è `true` solo se letto sul sito ufficiale.
2. A ogni esecuzione il tracker scarica le pagine elencate in `watch`, le
   confronta con l'ultimo snapshot (`data/snapshots/`) e segnala:
   - le pagine modificate, con il diff e le **date nuove comparse nel testo**;
   - le pagine non raggiungibili (sito cambiato o blocco anti-bot).
3. Il report è pubblicato come issue con l'etichetta `deadline-report`,
   assegnata al proprietario del repository (così arriva la notifica email).
   I report precedenti vengono chiusi.

## Ricerca automatica con Gemini

Se è configurato il secret `GEMINI_API_KEY`, per ogni congresso il tracker
passa a Gemini il testo delle pagine monitorate (più fino a 3 sottopagine
trovate tramite link come "abstract", "registration", "iscrizioni") e chiede
date, sede e scadenze in formato JSON strutturato.

Ogni dato deve essere accompagnato da una **citazione letterale** della
pagina. Il dato viene scartato se la citazione non compare nel testo
scaricato o non contiene il giorno e il mese della data. I dati accettati
finiscono in `data/extracted.json` e nel report con 🤖 (da verificare); i dati
scritti a mano in `conferences.yaml` hanno sempre la precedenza.

La ricerca viene saltata quando non serve:

| Situazione | Azione |
|---|---|
| congresso tra meno di 30 giorni | saltata: deadline quasi certamente scadute |
| congresso tra più di 400 giorni (~13 mesi) | saltata: deadline di solito non pubblicate |
| abstract ed early bird già presenti e pagine invariate | saltata |
| nessuna edizione futura nota | cerca la prossima edizione |

Il modello predefinito è `gemini-flash-latest`; si può cambiare con la
variabile di repository `GEMINI_MODEL`.

## Aggiornare i dati

Esempio di edizione completa:

```yaml
editions:
  - year: 2027
    city: San Diego
    country: USA
    venue: San Diego Convention Center
    start: 2027-05-02
    end: 2027-05-06
    website: https://www.arvo.org/annual-meeting/
    verified: true
    source: https://www.arvo.org/annual-meeting/
    deadlines:
      - type: abstract          # abstract_open | abstract | late_breaking | early_bird | registration
        date: 2026-12-06
        verified: true
        source: https://www.arvo.org/annual-meeting/abstracts/
```

Per aggiungere un congresso, copia un blocco esistente in `series`. Ogni push
avvia il workflow **Test**, che valida il file.

## Esecuzione

- Automatica: workflow **Report deadline congressi**, il 1° e il 15 del mese.
- Manuale: *Actions → Report deadline congressi → Run workflow*.
- In locale:

  ```bash
  pip install -e ".[dev]"
  python -m ophtha_tracker            # genera report.md e aggiorna gli snapshot
  python -m ophtha_tracker --validate-only
  pytest
  ```

## Configurazione

1. *Settings → Secrets and variables → Actions → New repository secret*:
   nome `GEMINI_API_KEY`, valore la chiave di Google AI Studio.
2. (Facoltativo) nella scheda *Variables*: `GEMINI_MODEL` per cambiare modello.

## Notifiche

Le issue assegnate a te generano una notifica email se in
*GitHub → Settings → Notifications* le notifiche email sono attive.
