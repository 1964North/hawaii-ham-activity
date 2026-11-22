#!/usr/bin/env python3
"""
Hawaii Ham Radio Activity - daily data collector.

Pulls public activity data for Hawaii amateur radio operators, merges it into
the stored history in ./history, and writes ./docs/data.json for the web page.

Sources
  FCC ULS amateur database   who is licensed in Hawaii (refreshed weekly)
  Reverse Beacon Network     CW / digital stations heard on the air (daily archives)
  PSK Reporter               FT8/FT4/JS8/... stations transmitting from Hawaii grids BK/BL
                             (6-hour bulk archive files at pskreporter.info/csv)
  Parks on the Air (POTA)    activations of Hawaii parks + hunter QSOs logged by activators
  Summits on the Air (SOTA)  activations of Hawaii summits
  40meter.net                check-ins to Hawaii HF, VHF/UHF and DMR nets (recent nets only -> run often)

Every source is collected separately; if one fails, the others still update and
the page keeps the last good data for the failed one.

Self-service opt-out: config.json (repository root) holds the Google Form link and the
published CSV of its responses: {"optout_form_url": "...", "optout_csv_url": "..."}

Usage:  python3 collector/collect.py            (run from the repository root)
Only the Python standard library is needed.
"""
import csv, collections, concurrent.futures, datetime as dt, glob, html, io, json, os, re, sys
import subprocess, tempfile, time, traceback, urllib.error, urllib.request, zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HIST = os.path.join(ROOT, 'history')
CACHE = os.path.join(ROOT, '.cache')
OUT = os.path.join(ROOT, 'docs', 'data.json')
UA = 'hawaii-ham-activity/1.0 (+https://github.com)'
HST = dt.timezone(dt.timedelta(hours=-10))
TODAY = dt.datetime.now(dt.timezone.utc).date()      # all days are UTC days, as in ham logging
WINDOW_DAYS = 365
KEEP_DAYS = 400                 # history kept on disk (a bit more than the page shows)
FCC_MAX_AGE_DAYS = 7
PROFILE_SCAN_DAYS = 7           # full scan of all Hawaii licensees for new POTA profiles

CALL = re.compile(r'^[A-Z0-9]{1,3}[0-9][A-Z0-9]*[A-Z]$')
ISLAND_ZIPS = {
    'Oahu': '96701 96706 96707 96709 96712 96717 96730 96731 96734 96744 96759 96762 96782 96786 96789 96791 96792 96795 96797',
    'Hawaii (Big Island)': '96704 96710 96718 96719 96720 96721 96725 96726 96727 96728 96737 96738 96739 96740 96743 96745 96749 96750 96755 96760 96764 96771 96772 96773 96774 96776 96777 96778 96780 96781 96783 96785',
    'Maui': '96708 96713 96732 96733 96753 96761 96767 96768 96779 96784 96788 96790 96793',
    'Kauai': '96703 96705 96714 96715 96716 96722 96741 96746 96747 96751 96752 96754 96756 96765 96766 96769 96796',
    'Molokai': '96729 96742 96748 96757 96770',
    'Lanai': '96763',
}
ZIP_ISLAND = {z: isl for isl, zs in ISLAND_ZIPS.items() for z in zs.split()}

# ---------------------------------------------------------------- helpers
def log(*a): print(*a, flush=True)

def base(call):
    """Strip portable prefixes/suffixes: KH6/N3GWR -> N3GWR, AH6K/W9 -> AH6K."""
    c = call.upper().strip()
    parts = [p for p in c.split('/') if CALL.match(p)]
    return max(parts, key=len) if parts else c

def http(url, timeout=60, tries=3, binary=False):
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': UA})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = r.read()
                return data if binary else data.decode('utf-8', errors='replace')
        except urllib.error.HTTPError as e:
            if e.code == 404: raise
            last = e
        except Exception as e:
            last = e
        time.sleep(3 * (i + 1))
    raise last

def get_json(url, **kw): return json.loads(http(url, **kw))

def load(name, default):
    p = os.path.join(HIST, name)
    if os.path.exists(p):
        with open(p) as f: return json.load(f)
    return default

def save(name, obj, pretty=False):
    p = os.path.join(HIST, name)
    tmp = p + '.tmp'
    with open(tmp, 'w') as f:
        json.dump(obj, f, indent=1 if pretty else None, separators=None if pretty else (',', ':'), sort_keys=pretty)
    os.replace(tmp, p)

status = load('status.json', {})
def ok(source, note=''):
    status[source] = {'updated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='minutes'), 'note': note}

# ---------------------------------------------------------------- FCC
def update_fcc():
    """Weekly: Hawaii licensees (+ island) and a callsign->state map for everyone."""
    os.makedirs(CACHE, exist_ok=True)
    state_path = os.path.join(CACHE, 'fcc_state.json')
    hi_path = os.path.join(HIST, 'hi_licensees.json')
    meta_path = os.path.join(CACHE, 'fcc_meta.json')   # file times are reset by the CI cache, so store the date
    fresh = False
    if os.path.exists(state_path) and os.path.exists(hi_path) and os.path.exists(meta_path):
        with open(meta_path) as f: got = dt.date.fromisoformat(json.load(f)['downloaded'])
        fresh = (TODAY - got).days < FCC_MAX_AGE_DAYS
    if fresh:
        log('FCC: cache is fresh'); return
    log('FCC: downloading l_amat.zip (~200 MB)')
    with tempfile.TemporaryDirectory() as td:
        zp = os.path.join(td, 'l_amat.zip')
        req = urllib.request.Request('https://data.fcc.gov/download/pub/uls/complete/l_amat.zip', headers={'User-Agent': UA})
        with urllib.request.urlopen(req, timeout=600) as r, open(zp, 'wb') as f:
            while True:
                b = r.read(1 << 20)
                if not b: break
                f.write(b)
        z = zipfile.ZipFile(zp)
        def rows(member):
            with z.open(member) as fh:
                for line in io.TextIOWrapper(fh, encoding='latin-1'):
                    yield line.rstrip('\r\n').split('|')
        active = {f[1] for f in rows('HD.dat') if len(f) > 5 and f[5] == 'A'}
        cls = {f[1]: f[5] for f in rows('AM.dat') if len(f) > 5}
        state, hi = {}, {}
        for f in rows('EN.dat'):
            if len(f) < 19 or f[1] not in active: continue
            call = f[4]
            state[call] = f[17]
            if f[17] == 'HI':
                z5 = f[18][:5]
                island = ZIP_ISLAND.get(z5) or ('Oahu' if z5.startswith('968') else 'Other')
                hi[call] = {'city': f[16].title(), 'cls': cls.get(f[1], ''), 'club': f[5] == 'CL', 'island': island}
    if len(hi) < 1000:
        raise RuntimeError(f'FCC: only {len(hi)} Hawaii licensees parsed - refusing to overwrite')
    with open(state_path, 'w') as f: json.dump(state, f, separators=(',', ':'))
    with open(meta_path, 'w') as f: json.dump({'downloaded': TODAY.isoformat()}, f)
    save('hi_licensees.json', hi)
    ok('fcc', f'{len(hi)} active Hawaii licenses')
    log(f'FCC: {len(hi)} Hawaii licensees, {len(state)} total')

def fcc_state():
    p = os.path.join(CACHE, 'fcc_state.json')
    if os.path.exists(p):
        with open(p) as f: return json.load(f)
    return {}

# ---------------------------------------------------------------- RBN
def update_rbn(hi):
    """Download any missing daily RBN archives in the window and keep Hawaii rows.
    Stored row: [call, date, bucket(cw|data), spots, distinct_skimmers]."""
    rbn = load('rbn.json', {'rows': [], 'portable': {}, 'days_done': []})
    done = set(rbn['days_done'])
    start = TODAY - dt.timedelta(days=WINDOW_DAYS)
    want = [start + dt.timedelta(days=i) for i in range(WINDOW_DAYS + 1)]
    missing = [d for d in want if d.isoformat() not in done and d < TODAY]
    log(f'RBN: {len(missing)} day(s) to fetch')
    hi_set = set(hi)
    new_rows = []
    for d in missing:
        url = f'https://data.reversebeacon.net/rbn_history/{d:%Y%m%d}.zip'
        try:
            data = http(url, timeout=180, binary=True)
        except urllib.error.HTTPError:
            log(f'RBN: {d} not published yet'); continue
        agg = collections.Counter(); sk = collections.defaultdict(set)
        z = zipfile.ZipFile(io.BytesIO(data))
        with z.open(z.namelist()[0]) as fh:
            for row in csv.reader(io.TextIOWrapper(fh, encoding='latin-1')):
                if len(row) < 13 or row[0] == 'callsign': continue
                dx, kind = row[5].upper(), row[8].upper()
                if 'BEACON' in kind or 'NCDXF' in kind or kind == 'B' or dx.endswith('/B'): continue
                b = base(dx)
                if not (row[6] == 'KH6' or b in hi_set): continue
                if b in hi_set and row[6] != 'KH6' and '/' in dx: continue   # resident portable elsewhere
                bucket = 'cw' if row[12].upper() == 'CW' else 'data'
                k = (b, row[10][:10], bucket)
                agg[k] += 1; sk[k].add(row[0])
                if b != dx: rbn['portable'][b] = dx
        new_rows += [[k[0], k[1], k[2], n, len(sk[k])] for k, n in agg.items()]
        done.add(d.isoformat())
    cutoff = (TODAY - dt.timedelta(days=KEEP_DAYS)).isoformat()
    rbn['rows'] = [r for r in rbn['rows'] + new_rows if r[1] >= cutoff]
    rbn['days_done'] = sorted(x for x in done if x >= cutoff)
    save('rbn.json', rbn)
    ok('rbn', f'through {max(rbn["days_done"]) if rbn["days_done"] else "-"}')


# ---------------------------------------------------------------- PSK Reporter
PSK_BACKFILL_DAYS = int(os.environ.get('PSK_BACKFILL_DAYS', '3'))
PSK_SKIP_MODES = {'WSPR', 'FST4W', 'WSPR2', 'Q65W'}       # beacon-style, not contacts

def psk_files():
    """List archive files (start time, url) for this and last month."""
    now = dt.datetime.now(dt.timezone.utc)
    files = []
    for m in {(now.year, now.month), ((now.replace(day=1) - dt.timedelta(days=1)).year, (now.replace(day=1) - dt.timedelta(days=1)).month)}:
        idx = http(f'https://pskreporter.info/csv/{m[0]}/{m[1]:02d}/', timeout=60)
        for name in set(re.findall(r'(\d{8}T\d{6}_(?:even|odd)\.tsv\.gz)', idx)):
            t = dt.datetime.strptime(name[:15], '%Y%m%dT%H%M%S').replace(tzinfo=dt.timezone.utc)
            files.append((t, name, f'https://pskreporter.info/csv/{m[0]}/{m[1]:02d}/{name}'))
    return sorted(files)

def psk_filter(url):
    """Stream one archive file and keep reports whose sender is in a Hawaii grid (BK/BL)."""
    cmd = (f"curl -sS --fail --retry 2 --retry-delay 10 --max-time 1200 -A '{UA}' '{url}' "
           f"| gunzip -c | awk -F'\\t' '$6 ~ /^(BK|BL)/'")
    r = subprocess.run(['bash', '-o', 'pipefail', '-c', cmd], capture_output=True, text=True, errors='replace')
    if r.returncode != 0: raise RuntimeError(f'{url.rsplit("/", 1)[-1]}: exit {r.returncode}: {r.stderr.strip()[:250]}')
    return [l.split('\t') for l in r.stdout.splitlines()]

def update_psk():
    psk = load('psk.json', {'rows': [], 'files_done': []})
    done = set(psk['files_done'])
    now = dt.datetime.now(dt.timezone.utc)
    oldest = now - dt.timedelta(days=PSK_BACKFILL_DAYS)
    todo = [f for f in psk_files() if f[1] not in done and f[0] >= oldest
            and f[0] + dt.timedelta(hours=6, minutes=20) < now]          # only complete files
    log(f'PSK: {len(todo)} file(s) to process')
    agg = {(r[0], r[1], r[2]): r for r in psk['rows']}
    errors = []
    for t, name, url in todo:
        per = collections.Counter(); rx = collections.defaultdict(set)
        try:
            reports = psk_filter(url)
        except Exception as e:
            errors.append(str(e)); log(f'PSK: {e}')
            if len(errors) >= 3: break            # server unhappy - stop, retry next run
            continue
        for f in reports:
            if len(f) < 8 or f[0] == 'flowStartSeconds' or f[3].upper() in PSK_SKIP_MODES: continue
            call = base(f[4]); day = dt.datetime.fromtimestamp(int(f[0]), dt.timezone.utc).date().isoformat()
            k = (call, day, 'cw' if f[3].upper() == 'CW' else 'data')
            per[k] += 1; rx[k].add(f[6].upper())
        for k, n in per.items():
            r = agg.setdefault(k, [k[0], k[1], k[2], 0, 0])
            r[3] += n; r[4] = max(r[4], len(rx[k]))
        done.add(name)
        psk['rows'] = list(agg.values()); psk['files_done'] = sorted(done)
        save('psk.json', psk)                                             # save after every file
        log(f'PSK: {name}: {sum(per.values())} Hawaii reports')
    cutoff = (TODAY - dt.timedelta(days=KEEP_DAYS)).isoformat()
    psk['rows'] = [r for r in agg.values() if r[1] >= cutoff]
    psk['files_done'] = sorted(x for x in done if x[:8] >= cutoff.replace('-', ''))
    save('psk.json', psk)
    if todo and len(errors) >= len(todo) or len(errors) >= 3:
        raise RuntimeError(f'{len(errors)} file(s) failed, e.g. {errors[0]}')
    ok('psk', f'{len(psk["files_done"])} archive files processed' + (f', {len(errors)} failed' if errors else ''))

# ---------------------------------------------------------------- POTA / SOTA
def update_pota_activations():
    parks = get_json('https://api.pota.app/location/parks/US-HI')
    acts = []
    for p in parks:
        for a in get_json(f"https://api.pota.app/park/activations/{p['reference']}?count=all") or []:
            acts.append({'call': a['activeCallsign'], 'date': a['qso_date'], 'cw': a['qsosCW'],
                         'data': a['qsosDATA'], 'phone': a['qsosPHONE'], 'park': p['reference']})
        time.sleep(0.2)
    if len(acts) < 100: raise RuntimeError(f'POTA: only {len(acts)} activations - keeping old data')
    save('pota_activations.json', acts)
    ok('pota', f'{len(parks)} parks, {len(acts)} activations')
    log(f'POTA: {len(acts)} activations in {len(parks)} parks')

def update_sota():
    assoc = get_json('https://api2.sota.org.uk/api/associations/KH6')
    acts = []
    for r in assoc['regions']:
        reg = get_json(f"https://api2.sota.org.uk/api/regions/KH6/{r['regionCode']}")
        for s in reg['summits']:
            for a in get_json(f"https://api2.sota.org.uk/api/activations/{s['summitCode']}") or []:
                acts.append({'call': a['ownCallsign'], 'date': a['activationDate'][:10], 'qsos': a['qsos'], 'summit': s['summitCode']})
            time.sleep(0.2)
    if len(acts) < 50: raise RuntimeError(f'SOTA: only {len(acts)} activations - keeping old data')
    save('sota.json', acts)
    ok('sota', f'{len(acts)} activations')
    log(f'SOTA: {len(acts)} activations')

def fetch_profile(call):
    try:
        d = get_json('https://api.pota.app/profile/' + call, timeout=30)
        return call, d if isinstance(d, dict) else None
    except urllib.error.HTTPError:
        return call, None
    except Exception:
        return call, 'error'

def update_pota_hunters(hi):
    """Hunter QSOs (logged by activators) from public POTA profiles: last 25 per ham.
    Known profiles are checked daily; all Hawaii licensees are re-scanned weekly."""
    idx = load('pota_profiles.json', {'profiles': [], 'last_full_scan': ''})
    hunts = load('pota_hunter.json', [])
    seen = {tuple(h[:6]) for h in hunts}
    full = not idx['last_full_scan'] or \
        (TODAY - dt.date.fromisoformat(idx['last_full_scan'])).days >= PROFILE_SCAN_DAYS
    calls = sorted(hi) if full else idx['profiles']
    log(f'POTA hunters: checking {len(calls)} callsigns ({"full scan" if full else "known profiles"})')
    found, added, errors = set(), 0, 0
    cutoff = (TODAY - dt.timedelta(days=KEEP_DAYS)).isoformat()
    with concurrent.futures.ThreadPoolExecutor(4) as ex:
        for call, prof in ex.map(fetch_profile, calls):
            if prof == 'error': errors += 1; found.add(call) if call in idx['profiles'] else None; continue
            if not prof: continue
            found.add(call)
            for h in prof.get('recent_activity', {}).get('hunter_qsos', []):
                k = (call, h['date'], h['callsign'], h['reference'], h['band'], h['mode'])
                if k not in seen and h['date'][:10] >= cutoff:      # ignore contacts older than the kept history
                    seen.add(k); hunts.append(list(k)); added += 1
    if full:
        if errors > len(calls) * 0.2: raise RuntimeError(f'POTA hunters: {errors} errors during full scan')
        idx = {'profiles': sorted(found), 'last_full_scan': TODAY.isoformat()}
    else:
        idx['profiles'] = sorted(set(idx['profiles']) | found)
    hunts = [h for h in hunts if h[1][:10] >= cutoff]
    save('pota_profiles.json', idx, pretty=True)
    save('pota_hunter.json', hunts)
    ok('pota_hunter', f'{len(idx["profiles"])} profiles, +{added} new QSOs')
    log(f'POTA hunters: {len(idx["profiles"])} profiles, {added} new QSOs')

# ---------------------------------------------------------------- 40meter.net
def parse_net_log(text):
    text = html.unescape(re.sub(r'<[^>]+>', '\n', text))
    nets = []
    for block in text.split('########### Next Net')[1:]:
        n = {'date': None, 'net': '', 'freq': '', 'ncs': '', 'opened': '', 'calls': []}
        for line in block.splitlines():
            l = line.strip()
            if not l or l.startswith('#####'): continue
            if l.startswith('#'):
                m = re.match(r'#\s*Start:\s*(\d\d)-(\d\d)-(\d{4})', l)
                if m: n['date'] = f'{m[3]}-{m[1]}-{m[2]}'
                m = re.match(r'#\s*Roster opened by\s+(\S+)', l)
                if m: n['ncs'] = m[1].upper()
                m = re.match(r'#\s*NET:\s*(.+?)(?:\s+on\s+([\d.]+))?\s*$', l)
                if m: n['net'], n['freq'] = m[1].strip(), m[2] or ''
                m = re.match(r'#\s*Opened:\s*(.+)', l)
                if m: n['opened'] = m[1].strip()
                continue
            c = re.sub(r'[^A-Z0-9/]', '', l.split()[0].upper()).strip('/')   # drop stray characters
            if CALL.match(base(c)) and c not in n['calls']:                    # also KH6/G3SEA-style calls
                n['calls'].append(c)
        if n['date'] and n['calls']: nets.append(n)
    return nets

NET40_LOGS = {   # 40meter.net log pages (same format); each shows only recent nets -> run often
    'hf': 'https://40meter.net/hf_log.cgi',                      # Aloha, Hi-Noon, Afternoon ... nets
    'vhf': 'https://40meter.net/vhf_uhf_log_display.cgi',        # VHF/UHF, DMR, C4FM, ARES ... nets
    'hf2': 'https://40meter.net/hf-2_log_display.cgi',           # 10-10 net, HF hospital net ...
}

def update_net40():
    nets, failed = [], []
    for log_name, url in NET40_LOGS.items():
        try:
            got = parse_net_log(http(url))
            for n in got: n['log'] = log_name
            nets += got
            log(f'40meter.net {log_name}: {len(got)} nets on page')
        except Exception as e:
            failed.append(log_name); log(f'40meter.net {log_name} failed: {e}')
    if len(failed) == len(NET40_LOGS): raise RuntimeError('40meter.net: all log pages failed')
    stored = load('net40.json', [])
    key = lambda n: (n.get('log', 'hf'), n['date'], n['net'], n['freq'], n.get('opened', ''), n['ncs'])
    merged = {key(n): n for n in stored}
    for n in nets: merged[key(n)] = n        # newer copy wins (logs can be edited during the month)
    cutoff = (TODAY - dt.timedelta(days=KEEP_DAYS)).isoformat()
    out = sorted((n for n in merged.values() if n['date'] >= cutoff), key=lambda n: (n['date'], n.get('opened', '')))
    save('net40.json', out, pretty=False)
    ok('net40', f'{len(nets)} nets on the log pages, {len(out)} stored' + (f' (failed: {", ".join(failed)})' if failed else ''))
    log(f'40meter.net: {len(out)} nets stored')


# ---------------------------------------------------------------- self-service opt-out (Google Form)
def load_config():
    p = os.path.join(ROOT, 'config.json')
    if os.path.exists(p):
        with open(p) as f: return json.load(f)
    return {}

def update_optout():
    """Read the published Google Form responses (CSV) and apply them in time order.
    Requests: hide/show in ranking + awards, hide/show name on certificates.
    If the sheet can't be read, the last known lists stay in force (nobody is un-hidden by accident)."""
    cfg = load_config(); url = cfg.get('optout_csv_url', '').strip()
    state = load('optout.json', {'hidden': [], 'no_name': []})
    if not url:
        ok('optout', 'no form configured'); return
    rows = list(csv.reader(io.StringIO(http(url, timeout=60))))
    if not rows: raise RuntimeError('opt-out sheet is empty or unreadable')
    head = [h.strip().lower() for h in rows[0]]
    ci = next((i for i, h in enumerate(head) if 'call' in h), None)
    ri = next((i for i, h in enumerate(head) if 'request' in h or 'what would you like' in h), None)
    ti = next((i for i, h in enumerate(head) if 'timestamp' in h), 0)
    if ci is None or ri is None: raise RuntimeError(f'opt-out sheet: callsign/request columns not found in {rows[0]}')
    def ts(r):
        for fmt in ('%m/%d/%Y %H:%M:%S', '%Y-%m-%d %H:%M:%S', '%d/%m/%Y %H:%M:%S'):
            try: return dt.datetime.strptime(r[ti].strip(), fmt)
            except Exception: pass
        return dt.datetime.min
    hidden, no_name = set(state['hidden']), set(state['no_name'])
    for r in sorted(rows[1:], key=ts):
        if len(r) <= max(ci, ri): continue
        call = base(re.sub(r'[^A-Za-z0-9/]', '', r[ci]).upper())
        req = r[ri].lower()
        if not CALL.match(call): continue
        if 'hide me' in req: hidden.add(call)
        elif 'show me' in req: hidden.discard(call)
        elif 'hide my name' in req: no_name.add(call)
        elif 'show my name' in req: no_name.discard(call)
    state = {'hidden': sorted(hidden), 'no_name': sorted(no_name)}
    save('optout.json', state, pretty=True)
    ok('optout', f'{len(hidden)} callsign(s) hidden, {len(no_name)} name(s) hidden')
    log(f'Opt-out: {len(hidden)} hidden, {len(no_name)} names hidden')

# ---------------------------------------------------------------- build data.json
def net_utc_date(n):
    """40meter.net logs give the Hawaii date and opening time; convert to the UTC date."""
    d = dt.date.fromisoformat(n['date'])
    m = re.match(r'(\d{1,2}):(\d\d)\s*([AP]M)', n.get('opened', '').upper())
    if not m: return d
    h = int(m[1]) % 12 + (12 if m[3] == 'PM' else 0)
    local = dt.datetime(d.year, d.month, d.day, h, int(m[2]), tzinfo=HST)
    return local.astimezone(dt.timezone.utc).date()

def build():
    hi = load('hi_licensees.json', {})
    fstate = fcc_state()
    rbn = load('rbn.json', {'rows': [], 'portable': {}})
    start = TODAY - dt.timedelta(days=WINDOW_DAYS - 1)
    MODES = {'cw': 0, 'data': 1, 'voice': 2, 'unk': 3}
    SRC = {'rbn': 0, 'pota': 1, 'sota': 2, 'psk': 3, 'net': 4, 'hunt': 5}
    ev = collections.defaultdict(lambda: [0, 0, 0, 0])   # qsos, spots, checkins, nets_run
    seen_hi_op = set()

    def add(call, day, mode, src, q=0, s=0, ci=0, nr=0):
        if not (start <= day <= TODAY): return
        v = ev[(call, (day - start).days, MODES[mode], SRC[src])]
        v[0] += q; v[1] += s; v[2] += ci; v[3] += nr

    # RBN - the KH6 prefix comes from the callsign, not the location, so drop calls
    # registered outside Hawaii (unless signing /KH6) and likely mis-decoded calls.
    window = start.isoformat()
    rows = [r for r in rbn['rows'] if r[1] >= window]
    max_sk = collections.defaultdict(int); days = collections.defaultdict(set)
    for c, d, b, s, k in rows: max_sk[c] = max(max_sk[c], k); days[c].add(d)
    for c, d, b, s, k in rows:
        port = rbn['portable'].get(c, '')
        if c in hi: pass
        elif c in fstate:
            if 'KH6' not in port and 'KH7' not in port: continue
            seen_hi_op.add(c)
        elif not (max_sk[c] >= 3 and (len(days[c]) >= 2 or max_sk[c] >= 5)):
            continue
        add(c, dt.date.fromisoformat(d), b, 'rbn', s=s)

    # PSK Reporter - sender located in a Hawaii grid, so location is known
    prow = [r for r in load('psk.json', {'rows': []})['rows'] if r[1] >= window]
    pmax = collections.defaultdict(int)
    for c, d, b, s, k in prow: pmax[c] = max(pmax[c], k)
    for c, d, b, s, k in prow:
        if c not in hi and c not in fstate and pmax[c] < 3: continue        # likely mis-decoded
        if c not in hi: seen_hi_op.add(c)
        add(c, dt.date.fromisoformat(d), b, 'psk', s=s)
    for a in load('pota_activations.json', []):
        c = base(a['call']); d = dt.datetime.strptime(a['date'], '%Y%m%d').date()
        seen_hi_op.add(c)
        for m, f in (('cw', 'cw'), ('data', 'data'), ('voice', 'phone')):
            if a[f]: add(c, d, m, 'pota', q=a[f])
    for a in load('sota.json', []):
        c = base(a['call']); seen_hi_op.add(c)
        add(c, dt.date.fromisoformat(a['date']), 'unk', 'sota', q=a['qsos'])
    HM = {'CW': 'cw', 'DATA': 'data', 'PHONE': 'voice', 'SSB': 'voice', 'FM': 'voice', 'AM': 'voice'}
    for call, date, _, _, _, mode in load('pota_hunter.json', []):
        add(call, dt.date.fromisoformat(date[:10]), HM.get(mode.upper(), 'data'), 'hunt', q=1)
    net_seen, net_calls = set(), set()
    for n in load('net40.json', []):
        d = net_utc_date(n)
        name = n['net'].upper()
        mode = 'data' if any(w in name for w in ('WINLINK', 'DIGITAL', 'JS8', 'FT8', 'PACKET', 'VARA')) else 'voice'
        for c in n['calls']:
            b = base(c)
            k = (b, d, name, n.get('log', 'hf'))              # same net logged twice -> count once
            if k in net_seen: continue
            net_seen.add(k); net_calls.add(b)
            add(b, d, mode, 'net', ci=1, nr=1 if b == base(n['ncs']) else 0)

    optout = load('optout.json', {'hidden': [], 'no_name': []})
    hidden = set(optout['hidden'])
    for k in [k for k in ev if k[0] in hidden]: del ev[k]          # opted out: not shown at all
    calls = sorted({k[0] for k in ev})
    idx = {c: i for i, c in enumerate(calls)}
    stations = []
    for c in calls:
        if c in hi: stations.append([c, 'R', hi[c]['island'], hi[c]['city']])
        elif c in fstate: stations.append([c, 'V', '', 'Lic. in ' + fstate[c] + ('' if c in seen_hi_op else ' · net check-ins')])
        elif c in seen_hi_op: stations.append([c, 'V', '', ''])
        elif c in net_calls: stations.append([c, 'V', '', 'Net check-ins'])   # e.g. foreign or new calls on a Hawaii net
        else: stations.append([c, 'U', '', ''])
    out = {
        'asof': TODAY.isoformat(), 'start': start.isoformat(),
        'generated': dt.datetime.now(dt.timezone.utc).isoformat(timespec='minutes').replace('+00:00', ''),
        'licensed': len(hi), 'licensedByIsland': collections.Counter(v['island'] for v in hi.values()),
        'sources': status, 'stations': stations,
        'noName': [c for c in optout['no_name'] if c in idx],
        'forms': {'optout': load_config().get('optout_form_url', '')},
        'events': [[idx[k[0]], k[1], k[2], k[3], *v] for k, v in ev.items()],
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT + '.tmp', 'w') as f: json.dump(out, f, separators=(',', ':'))
    os.replace(OUT + '.tmp', OUT)
    log(f'data.json: {len(stations)} stations, {len(out["events"])} events')

# ---------------------------------------------------------------- main
def main():
    os.makedirs(HIST, exist_ok=True)
    only = set(sys.argv[1:])          # e.g. "net40 build" to run just those steps
    failures = []
    def step(name, fn, *a):
        if only and name not in only: return
        try:
            fn(*a)
            if name in status: status[name].pop('error', None)
        except Exception as e:
            failures.append(name); log(f'!! {name} failed:'); traceback.print_exc()
            status.setdefault(name, {})['error'] = {
                'at': dt.datetime.now(dt.timezone.utc).isoformat(timespec='minutes'),
                'message': f'{type(e).__name__}: {e}'[:400]}
    step('fcc', update_fcc)
    hi = load('hi_licensees.json', {})
    step('net40', update_net40)
    step('rbn', update_rbn, hi)
    step('psk', update_psk)
    step('pota', update_pota_activations)
    step('sota', update_sota)
    step('pota_hunter', update_pota_hunters, hi)
    step('optout', update_optout)
    save('status.json', status, pretty=True)
    step('build', build)
    if failures: log('Failed steps:', ', '.join(failures))
    if 'build' in failures: sys.exit(1)

if __name__ == '__main__':
    main()
