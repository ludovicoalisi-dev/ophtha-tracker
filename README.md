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

Il tracker **non modifica da solo le date**: quando una pagina cambia, verifichi
il diff e aggiorni il file YAML. È una scelta voluta: l'estrazione automatica
da siti eterogenei produce errori silenziosi, e una scadenza sbagliata è peggio
di una scadenza mancante.

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

## Notifiche

Le issue assegnate a te generano una notifica email se in
*GitHub → Settings → Notifications* le notifiche email sono attive.
