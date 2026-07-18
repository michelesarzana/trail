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

def fetch_health_snapshot():
    """Scarica snapshot salute di oggi e ieri da Garmin Connect."""
    today = datetime.now().strftime('%Y-%m-%d')
    yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    
    health = {}
    
    # Passi e stress giornalieri (user summary)
    try:
        url = f"https://connectapi.garmin.com/usersummary-service/usersummary/daily/{today}?fromDate={today}&untilDate={today}"
        data = garmin_get(url)
        health['steps'] = data.get('totalSteps', 0)
        health['steps_goal'] = data.get('dailyStepGoal', 8000)
        health['calories'] = data.get('totalKilocalories', 0)
        health['active_calories'] = data.get('activeKilocalories', 0)
        health['resting_hr'] = data.get('restingHeartRate', 0)
        health['stress_avg'] = data.get('averageStressLevel', 0)
        health['body_battery_end'] = data.get('bodyBatteryMostRecentValue', 0)
        health['sleep_hours'] = round(data.get('sleepingSeconds', 0) / 3600, 1)
        health['active_min'] = data.get('moderateIntensityMinutes', 0) + data.get('vigorousIntensityMinutes', 0) * 2
        health['floors'] = data.get('floorsAscended', 0)
    except Exception as e:
        print(f"  Health summary non disponibile: {e}")
    
    # HRV settimanale
    try:
        url = f"https://connectapi.garmin.com/hrv-service/hrv/{today}"
        data = garmin_get(url)
        hrv = data.get('hrvSummary', {})
        health['hrv_weekly_avg'] = hrv.get('weeklyAvg', 0)
        health['hrv_last_night'] = hrv.get('lastNight', 0)
        health['hrv_status'] = hrv.get('hrvStatus', '')  # 'BALANCED', 'LOW', 'HIGH'
    except Exception as e:
        print(f"  HRV non disponibile: {e}")
    
    # SpO2
    try:
        url = f"https://connectapi.garmin.com/wellness-service/wellness/dailyOximetry/{today}"
        data = garmin_get(url)
        readings = data.get('oximetryReadings', [])
        if readings:
            vals = [r.get('spO2Value', 0) for r in readings if r.get('spO2Value', 0) > 0]
            health['spo2_avg'] = round(sum(vals)/len(vals), 1) if vals else None
        else:
            health['spo2_avg'] = None
    except Exception as e:
        print(f"  SpO2 non disponibile: {e}")
        health['spo2_avg'] = None
    
    # Respiration rate
    try:
        url = f"https://connectapi.garmin.com/wellness-service/wellness/dailyRespirationRate/{today}"
        data = garmin_get(url)
        health['respiration_avg'] = round(data.get('avgBreathingFrequency', 0), 1) or None
    except Exception as e:
        print(f"  Respiration non disponibile: {e}")
        health['respiration_avg'] = None
    
    # Fitness Age
    try:
        url = f"https://connectapi.garmin.com/metrics-service/metrics/fitnessAge/{today}"
        data = garmin_get(url)
        health['fitness_age'] = data.get('fitnessAge', 0) or data.get('value', 0) or None
        health['chronological_age'] = data.get('chronologicalAge', 0) or None
    except Exception as e:
        print(f"  Fitness age non disponibile: {e}")
        health['fitness_age'] = None
        health['chronological_age'] = None
    
    # Training Status
    try:
        url = f"https://connectapi.garmin.com/metrics-service/metrics/trainingStatus/{today}"
        data = garmin_get(url)
        ts = data.get('trainingStatus', '') or data.get('mostRecentTrainingStatus', '')
        ts_load = data.get('trainingLoadFeedback', '') or data.get('acuteLoad', '')
        health['training_status'] = ts or None
        health['training_load_feedback'] = ts_load or None
        health['acute_load'] = data.get('acuteTrainingLoad', 0) or None
        health['chronic_load'] = data.get('chronicTrainingLoad', 0) or None
    except Exception as e:
        print(f"  Training status non disponibile: {e}")
        health['training_status'] = None
        health['training_load_feedback'] = None
        health['acute_load'] = None
        health['chronic_load'] = None
    
    health['date'] = today
    return health

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

print("Scaricando health snapshot...")
try:
    health_snap = fetch_health_snapshot()
    print(f"  Health: steps={health_snap.get('steps')}, resting_hr={health_snap.get('resting_hr')}, hrv={health_snap.get('hrv_last_night')}")
except Exception as e:
    print(f"  Health snapshot fallito: {e}")
    health_snap = {}

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
    'health_today': health_snap,
}

with open('performance.json', 'w') as f:
    json.dump(out, f, indent=2, ensure_ascii=False)

print(f"performance.json generato: {len(all_acts)} attivita totali, {len(by_sport)} sport")
for s, acts_s in by_sport.items():
    print(f"  {s}: {len(acts_s)} sessioni")
