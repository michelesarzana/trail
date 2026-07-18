#!/usr/bin/env python3
"""
garmin_sync.py — Scarica dati da Garmin Connect e genera performance.json
Usato da GitHub Actions come cron giornaliero.
Richiede variabile d'ambiente: GARMIN_TOKEN (JSON string del token)
"""
import json, os, sys, urllib.request, urllib.error, urllib.parse
from datetime import datetime, timedelta


def refresh_di_token(token_data):
    """Rinnova il token DI di Garmin usando il refresh_token."""
    url = "https://diauth.garmin.com/di-oauth2-service/oauth/token"
    payload = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "refresh_token": token_data["di_refresh_token"],
        "client_id": token_data["di_client_id"],
    }).encode("utf-8")
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "GCM-iOS-5.7.2.1 (com.garmin.connect.mobile; build:5.7.2.1; iOS 16.6.0)",
    }
    try:
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read())
        token_data["di_token"] = result["access_token"]
        if result.get("refresh_token"):
            token_data["di_refresh_token"] = result["refresh_token"]
        print("Token DI rinnovato con successo")
    except Exception as e:
        print(f"ATTENZIONE: refresh token DI fallito: {e}")
    return token_data


def update_github_secret(token_data, github_token):
    """Cifra il token con la public key del repo e aggiorna il secret GARMIN_TOKEN."""
    # 1. Installa PyNaCl se necessario
    try:
        from nacl import encoding, public
    except ImportError:
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "install", "PyNaCl", "-q"])
        from nacl import encoding, public

    # 2. Legge la public key del repo
    pk_url = "https://api.github.com/repos/michelesarzana/trail/actions/secrets/public-key"
    pk_req = urllib.request.Request(pk_url, headers={
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "garmin-sync-bot",
    })
    with urllib.request.urlopen(pk_req, timeout=30) as resp:
        pk_data = json.loads(resp.read())
    key_id = pk_data["key_id"]
    public_key_b64 = pk_data["key"]

    # 3. Cifra il token con sealed box (libsodium via PyNaCl)
    import base64
    pk_bytes = base64.b64decode(public_key_b64)
    sealed_box = public.SealedBox(public.PublicKey(pk_bytes))
    encrypted = sealed_box.encrypt(json.dumps(token_data).encode("utf-8"))
    encrypted_value = base64.b64encode(encrypted).decode("utf-8")

    # 4. Aggiorna il secret su GitHub
    secret_url = "https://api.github.com/repos/michelesarzana/trail/actions/secrets/GARMIN_TOKEN"
    body = json.dumps({"encrypted_value": encrypted_value, "key_id": key_id}).encode("utf-8")
    put_req = urllib.request.Request(secret_url, data=body, method="PUT", headers={
        "Authorization": f"Bearer {github_token}",
        "Accept": "application/vnd.github.v3+json",
        "Content-Type": "application/json",
        "User-Agent": "garmin-sync-bot",
    })
    with urllib.request.urlopen(put_req, timeout=30) as resp:
        status = resp.status
    if status not in (201, 204):
        print(f"ATTENZIONE: update secret ha risposto HTTP {status}")
    else:
        print(f"Secret GARMIN_TOKEN aggiornato su GitHub (HTTP {status})")



# Token da env var (GitHub Actions secret)
token_str = os.environ.get('GARMIN_TOKEN', '')
if not token_str:
    print("ERROR: GARMIN_TOKEN env var non trovata")
    sys.exit(1)

token_data = json.loads(token_str)

# Rinnovo token DI
print("Rinnovo token Garmin...")
token_data = refresh_di_token(token_data)
di_token = token_data['di_token']

GARMIN_HEADERS = {
    "Authorization": f"Bearer {di_token}",
    "NK": "NT",
    "X-app-ver": "4.79.0.0",
    "User-Agent": "GCM-iOS-5.7.2.1 (com.garmin.connect.mobile; build:5.7.2.1; iOS 16.6.0)",
}

# Aggiorna il secret su GitHub con il token rinnovato
github_token = os.environ.get('GH_PAT', '') or os.environ.get('GITHUB_TOKEN', '')
if github_token:
    try:
        update_github_secret(token_data, github_token)
        print("Secret GitHub aggiornato")
    except Exception as e:
        print(f"ATTENZIONE: aggiornamento secret GitHub fallito: {e}")

def garmin_get(url):
    req = urllib.request.Request(url, headers=GARMIN_HEADERS)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

def fetch_health_snapshot():
    """Scarica snapshot salute da Garmin Connect.
    
    Endpoint verificati con token DI Android (2026-07-18):
    - usersummary-service: ?calendarDate=  (200, passi/calorie/distanza/stress/body battery/SpO2/respiration)
    - wellness/dailyHeartRate?date=         (200, FC min/max/resting)
    - hrv-service/hrv/{date}               (200 o 204 se assente)
    - sleep-service/stats/sleep/daily/     (200, sonno + SpO2 + respirazione + HRV)
    - wellness/dailyStress/{date}          (200, stress)
    
    Cerca fino a 3 giorni indietro perche il device puo non aver ancora sincronizzato oggi.
    """
    today = datetime.utcnow().strftime('%Y-%m-%d')
    # Build list of dates to try (today, yesterday, 2 days ago, 3 days ago)
    recent_dates = [(datetime.utcnow() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(4)]

    health = {}

    # --- 1. User Summary: passi, calorie, distanza, stress, body battery, SpO2, respiration ---
    try:
        for d in recent_dates:
            url = f"https://connectapi.garmin.com/usersummary-service/usersummary/daily?calendarDate={d}"
            try:
                data = garmin_get(url)
                if data.get('totalSteps') is not None:
                    health['steps'] = data.get('totalSteps', 0)
                    health['steps_goal'] = data.get('dailyStepGoal', 8000)
                    health['calories'] = data.get('totalKilocalories', 0)
                    health['active_calories'] = data.get('activeKilocalories', 0)
                    health['total_distance_m'] = data.get('totalDistanceMeters', 0)
                    health['floors'] = data.get('floorsAscended', 0)
                    health['active_min'] = (data.get('moderateIntensityMinutes', 0) or 0) + (data.get('vigorousIntensityMinutes', 0) or 0) * 2
                    health['resting_hr'] = data.get('restingHeartRate')
                    health['stress_avg'] = data.get('averageStressLevel')
                    health['body_battery_end'] = data.get('bodyBatteryMostRecentValue')
                    health['body_battery_max'] = data.get('bodyBatteryHighestValue')
                    health['body_battery_start'] = data.get('bodyBatteryAtWakeTime')
                    health['spo2_avg'] = data.get('averageSpo2')
                    health['spo2_min'] = data.get('lowestSpo2')
                    health['respiration_avg'] = data.get('avgWakingRespirationValue')
                    health['sleep_hours'] = round((data.get('sleepingSeconds') or 0) / 3600, 1)
                    health['summary_date'] = d
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"  User summary non disponibile: {e}")

    # --- 2. Heart Rate: FC resting/max/min ---
    try:
        for d in recent_dates:
            url = f"https://connectapi.garmin.com/wellness-service/wellness/dailyHeartRate?date={d}"
            try:
                data = garmin_get(url)
                if data.get('restingHeartRate') is not None:
                    health['resting_hr'] = data.get('restingHeartRate')
                    health['max_hr'] = data.get('maxHeartRate')
                    health['min_hr'] = data.get('minHeartRate')
                    health['hr_7d_avg_resting'] = data.get('lastSevenDaysAvgRestingHeartRate')
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"  Heart rate non disponibile: {e}")

    # --- 3. Sleep + SpO2 + Respiration + HRV (da sleep-service, fonte piu ricca) ---
    try:
        for d in recent_dates:
            url = f"https://connectapi.garmin.com/sleep-service/stats/sleep/daily/{d}/{d}"
            try:
                data = garmin_get(url)
                ind = data.get('individualStats') or []
                if ind:
                    sv = ind[0].get('values', {})
                    health['sleep_seconds'] = sv.get('totalSleepTimeInSeconds', 0)
                    health['sleep_hours'] = round((sv.get('totalSleepTimeInSeconds') or 0) / 3600, 1)
                    health['sleep_score'] = sv.get('sleepScore')
                    health['sleep_score_quality'] = sv.get('sleepScoreQuality')
                    health['sleep_deep_s'] = sv.get('deepTime', 0)
                    health['sleep_rem_s'] = sv.get('remTime', 0)
                    health['sleep_light_s'] = sv.get('lightTime', 0)
                    health['sleep_awake_s'] = sv.get('awakeTime', 0)
                    health['sleep_respiration'] = sv.get('respiration')
                    health['sleep_spo2'] = sv.get('spO2')
                    health['sleep_resting_hr'] = sv.get('restingHeartRate')
                    health['sleep_avg_hr'] = sv.get('avgHeartRate')
                    health['sleep_body_battery_change'] = sv.get('bodyBatteryChange')
                    health['hrv_last_night'] = sv.get('avgOvernightHrv')
                    health['hrv_7d_avg'] = sv.get('hrv7dAverage')
                    health['hrv_status'] = sv.get('hrvStatus')
                    health['skin_temp_c'] = sv.get('skinTempC')
                    health['sleep_date'] = d
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"  Sleep non disponibile: {e}")

    # --- 4. HRV summary (hrv-service, piu dettagliato) ---
    try:
        for d in recent_dates:
            url = f"https://connectapi.garmin.com/hrv-service/hrv/{d}"
            try:
                req = urllib.request.Request(url, headers=GARMIN_HEADERS)
                with urllib.request.urlopen(req, timeout=30) as r:
                    if r.status == 204:
                        continue
                    hrv_data = json.loads(r.read())
                    hrv_sum = hrv_data.get('hrvSummary', {})
                    if hrv_sum.get('lastNightAvg') is not None:
                        if not health.get('hrv_last_night'):
                            health['hrv_last_night'] = hrv_sum.get('lastNightAvg')
                        health['hrv_weekly_avg'] = hrv_sum.get('weeklyAvg')
                        health['hrv_5min_high'] = hrv_sum.get('lastNight5MinHigh')
                        if not health.get('hrv_status'):
                            health['hrv_status'] = hrv_sum.get('status')
                        health['hrv_date'] = d
                        break
            except Exception:
                continue
    except Exception as e:
        print(f"  HRV non disponibile: {e}")

    # --- 5. Stress ---
    try:
        for d in recent_dates:
            url = f"https://connectapi.garmin.com/wellness-service/wellness/dailyStress/{d}"
            try:
                data = garmin_get(url)
                if data.get('avgStressLevel') is not None:
                    health['stress_avg'] = data.get('avgStressLevel')
                    health['stress_max'] = data.get('maxStressLevel')
                    health['stress_date'] = d
                    break
            except Exception:
                continue
    except Exception as e:
        print(f"  Stress non disponibile: {e}")

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
            f"https://connectapi.garmin.com/activitylist-service/activities/search/activities"
            f"?start={start}&limit={limit}"
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

# GUARD: se Garmin restituisce 0 attività, non sovrascrivere i dati esistenti
if len(acts) == 0:
    print("ATTENZIONE: Garmin ha restituito 0 attività — probabile token scaduto silenziosamente.")
    try:
        with open('performance.json', 'r') as f:
            existing = json.load(f)
    except:
        existing = {}
    existing['token_expired'] = True
    existing['token_expired_at'] = datetime.now().strftime('%d/%m/%Y %H:%M')
    with open('performance.json', 'w') as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)
    print("Dati precedenti mantenuti, flag token_expired=true impostato.")
    sys.exit(1)

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
