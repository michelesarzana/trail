# 🏔️ trail

Mappa interattiva dei trail che voglio fare (e che ho già fatto).

## Struttura del progetto

```
trail/
├── schema/
│   └── trail.schema.json     # JSON Schema per la struttura di ogni trail
├── data/
│   └── trails.json           # Database principale dei trail
├── gpx/                      # File GPX delle tracce
├── media/                    # Foto e video per trail
│   └── <trail-id>/
└── index.html                # Sito web (coming soon)
```

## Database

Il database è in `data/trails.json`. Ogni trail segue lo schema definito in `schema/trail.schema.json`.

### Campi principali

| Campo | Tipo | Descrizione |
|-------|------|-------------|
| `id` | string | Slug univoco del trail |
| `title` | string | Nome del trail |
| `status` | `done` / `todo` | Completato o da fare |
| `rating` | 1–5 | Voto personale |
| `geo` | object | Paese, regione, provincia, comune, coordinate |
| `track` | object | Distanza, dislivello, quota, GPX, tipo percorso |
| `classification` | object | Difficoltà (scala EAC T1–T6), attività, stagioni, tag |
| `logistics` | object | Dog-friendly, mezzi pubblici, parcheggio, acqua |
| `links` | object | Strava, Komoot, AllTrails, Wikiloc |
| `media` | object | Foto, video, immagine di copertina |
| `logbook` | array | Storico uscite (data, note, meteo) |

### Scala difficoltà EAC/CAI

| Livello | Descrizione |
|---------|-------------|
| T1 | Turistico — sentieri evidenti, nessuna difficoltà |
| T2 | Escursionistico — sentieri con qualche difficoltà |
| T3 | Escursionistico avanzato — terreni impegnativi |
| T4 | Alpinistico — attrezzatura consigliata |
| T5 | Alpinistico avanzato — esperienza necessaria |
| T6 | Alpinistico estremo — tecnica alpinistica |

## Sito web

Coming soon — mappa interattiva con filtri per regione, difficoltà, status e tag.

---

*Trail database by Michele Sarzana*
