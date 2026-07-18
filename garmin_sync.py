#!/usr/bin/env python3
"""
garmin_sync.py — Scarica dati da Garmin Connect e genera performance.json
Usato da GitHub Actions come cron giornaliero.
Richiede variabile d'ambiente: GARMIN_TOKEN (JSON string del token)
"""
import json, os, sys, urllib.request, urllib.error
from datetime import datetime, timedelta

# Token da env var (GitHub Actions secret)
token_str = os.environ.get('GARMIN_TOKEN', '')
if not token_str:
    print("ERROR: GARMIN_TOKEN env var non trovata")
    sys.exit(1)

token_data = json.loads(token_str)
di_token = token_data['di_token']

GARMIN_HEADERS = {
    "Authorization": f"Bearer {di_token}",
    "NK": "NT",
    "X-app-ver": "4.79.0.0",
    "User-Agent": "GCM-iOS-5.7.2.1 (com.garmin.connect.mobile; build:5.7.2.1; iOS 16.6.0)",
}

def garmin_get(url):
    req = urllib.request.Request(url, headers=GARMIN_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def sport_label(act):
    t = act.get('activityType', {}).get('typeKey', 'other')
    name = act.get('activityName', '').lower()
    if 'trail' in t: return 'trail_running'
    if 'hiit' in t: return 'hiit'
    if 'cycling' in t or 'cycling' in name: return 'cycling'
    if 'running' in t: return 'running'
    if 'hiking' in t or 'hiking' in name: return 'hiking'
    if 'skate_skiing' in t: return 'skate_skiing_ws'
    if 'resort_skiing' in t or 'skiing' in t: return 'resort_skiing'
    if 'walking' in t: return 'walking'
    if 'multi_sport' in t: return 'multi_sport'
    return t

def parse_date(s):
    try: return datetime.strptime(s[:10], '%Y-%m-%d')
    except: return datetime(2000, 1, 1)

# --- FETCH TUTTE LE ATTIVITA (paginazione completa) ---
print("Scaricando tutte le attivita da Garmin...")

def fetch_all_activities():
    all_acts = []
    start = 0
    limit = 100
    while True:
        url = (
            f"https://connectapi.garmin.com/activity-service/activity/search/activities"
            f"?start={start}&limit={limit}&sortField=startLocal&sortOrder=DESC"
        )
        try:
            batch = garmin_get(url)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                raise
            print(f"  Errore HTTP {e.code} a start={start}, interrompo paginazione")
            break
        if not batch:
            break
        all_acts.extend(batch)
        print(f"  Scaricate {len(all_acts)} attivita...")
        if len(batch) < limit:
            break
        start += limit
    return all_acts

try:
    acts = fetch_all_activities()
except urllib.error.HTTPError as e:
    if e.code in (401, 403):
        print(f"TOKEN SCADUTO (HTTP {e.code}) — aggiorna il secret GARMIN_TOKEN su GitHub.")
        try:
            with open('performance.json', 'r') as f:
                existing = json.load(f)
        except:
            existing = {}
        existing['token_expired'] = True
        existing['token_expired_at'] = datetime.now().strftime('%d/%m/%Y %H:%M')
        with open('performance.json', 'w') as f:
            json.dump(existing, f, indent=2, ensure_ascii=False)
        print("performance.json aggiornato con flag token_expired=true")
        sys.exit(1)
    raise

print(f"  {len(acts)} attivita totali trovate")

now = datetime.now()
ago_90 = now - timedelta(days=90)

# Tutte le attività (storico completo)
all_acts = acts

# Ultimi 90 giorni per summary_90d
last90 = [a for a in all_acts if parse_date(a.get('startTimeLocal', '')) > ago_90]

# --- AGGREGATI 90gg ---
total_hours_90 = round(sum(a.get('duration', 0) for a in last90) / 3600, 1)
total_km_90    = round(sum(a.get('distance', 0) for a in last90) / 1000, 1)
total_elev_90  = int(sum(a.get('elevationGain', 0) for a in last90))
hrs_90 = [a.get('averageHR', 0) for a in last90 if a.get('averageHR', 0) > 0]
avg_hr_90 = int(sum(hrs_90) / len(hrs_90)) if hrs_90 else 0

# --- PER SPORT (tutte le attivita) ---
by_sport = {}
for a in all_acts:
    s = sport_label(a)
    if s not in by_sport:
        by_sport[s] = []
    by_sport[s].append({
        'id':            a.get('activityId'),
        'name':          a.get('activityName', ''),
        'date':          a.get('startTimeLocal', '')[:10],
        'distance_km':   round(a.get('distance', 0) / 1000, 2),
        'duration_min':  round(a.get('duration', 0) / 60, 1),
        'avg_hr':        int(a.get('averageHR') or 0),
        'elevation_m':   int(a.get('elevationGain') or 0),
        'avg_speed_kmh': round((a.get('averageSpeed', 0) or 0) * 3.6, 1),
    })

# --- VOLUME MENSILE (tutte le attivita) ---
monthly = {}
for a in all_acts:
    month = a.get('startTimeLocal', '')[:7]
    s = sport_label(a)
    if month not in monthly:
        monthly[month] = {}
    if s not in monthly[month]:
        monthly[month][s] = {'hours': 0, 'km': 0, 'sessions': 0}
    monthly[month][s]['hours']    = round(monthly[month][s]['hours'] + a.get('duration', 0) / 3600, 2)
    monthly[month][s]['km']       = round(monthly[month][s]['km']    + a.get('distance', 0) / 1000, 2)
    monthly[month][s]['sessions'] += 1

# --- VOLUME SETTIMANALE (tutte le attivita) ---
weekly = {}
for a in all_acts:
    d    = parse_date(a.get('startTimeLocal', ''))
    week = d.strftime('%Y-W%W')
    s    = sport_label(a)
    if week not in weekly:
        weekly[week] = {}
    weekly[week][s] = weekly[week].get(s, 0) + 1

# --- RECORD (tutte le attivita) ---
records = {
    'max_distance_km':  round(max((a.get('distance', 0) for a in all_acts), default=0) / 1000, 2),
    'max_elevation_m':  int(max((a.get('elevationGain', 0) for a in all_acts), default=0)),
    'max_duration_min': round(max((a.get('duration', 0) for a in all_acts), default=0) / 60, 1),
    'min_avg_hr':       int(min(
        (a.get('averageHR', 999) for a in all_acts if a.get('averageHR', 0) > 0), default=0
    )),
}

# --- OUTPUT ---
out = {
    'last_updated':  now.strftime('%d/%m/%Y %H:%M'),
    'token_expired': False,
    'summary_90d': {
        'sessions':           len(last90),
        'total_hours':        total_hours_90,
        'total_km':           total_km_90,
        'total_elevation_m':  total_elev_90,
        'avg_sessions_week':  round(len(last90) / (90 / 7), 1),
        'avg_hr':             avg_hr_90,
    },
    'records':        records,
    'by_sport':       by_sport,
    'monthly_volume': monthly,
    'weekly_volume':  weekly,
    'all_activities': [
        {
            'id':           a.get('activityId'),
            'name':         a.get('activityName', ''),
            'date':         a.get('startTimeLocal', '')[:10],
            'sport':        sport_label(a),
            'distance_km':  round(a.get('distance', 0) / 1000, 2),
            'duration_min': round(a.get('duration', 0) / 60, 1),
            'avg_hr':       int(a.get('averageHR') or 0),
            'elevation_m':  int(a.get('elevationGain') or 0),
        }
        for a in all_acts
    ],
}

with open('performance.json', 'w') as f:
    json.dump(out, f, indent=2, ensure_ascii=False)

print(f"performance.json generato: {len(all_acts)} attivita totali, {len(by_sport)} sport")
for s, acts_s in by_sport.items():
    print(f"  {s}: {len(acts_s)} sessioni")
