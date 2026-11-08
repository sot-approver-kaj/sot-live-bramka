#!/usr/bin/env python3
"""Wykonanie zatwierdzonego planu (ceny albo notatki zamówień). Fail-closed. Logi: wyłącznie liczby (repo publiczne).
Plan: prywatne repo B, plik dane/live/<sha256>.json; sha256 bajtów pliku musi = input. Wynik: tylko liczby w logu; kontrola po wykonaniu – niezależny odczyt (Browse) po stronie SM. Token repo B: tylko odczyt.
Ceny: ReviseInventoryStatus – wyłącznie ItemID + StartPrice. Notatki: SetUserNotes – ItemID + TransactionID + tekst ≤ 250.
"""
import os, re, sys, json, base64, hashlib, urllib.request as r, urllib.parse as u, datetime as dt
from xml.sax.saxutils import escape

def stop(m):
    print('STOP:', m); sys.exit(2)

SHA = os.environ.get('PLAN_SHA256', '').strip().lower()
ROD = os.environ.get('RODZAJ', '').strip()
REPO = os.environ.get('REPO_B', '').strip()
GT = os.environ.get('REPO_B_TOKEN', '')
if not re.fullmatch(r'[0-9a-f]{64}', SHA): stop('plan_sha256 niepoprawny')
if ROD not in ('ceny', 'notatki'): stop('rodzaj niepoprawny')
if os.environ.get('KOD_SHA', '').strip().lower() != os.environ.get('GITHUB_SHA', '').lower(): stop('kod_sha ≠ commit tego runu – nowe uruchomienie i nowe zatwierdzenie')
if not (REPO and GT and os.environ.get('EBAY_REFRESH_TOKEN') and os.environ.get('EBAY_APP_ID') and os.environ.get('EBAY_CERT_ID')): stop('brak sekretów/zmiennych środowiska live')

def gh(method, path, body=None):
    q = r.Request('https://api.github.com/repos/' + REPO + path, data=None if body is None else json.dumps(body).encode(), method=method,
                  headers={'Authorization': 'Bearer ' + GT, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'})
    try:
        x = r.urlopen(q, timeout=60); return x.status, json.loads(x.read() or b'{}')
    except Exception as e:
        return getattr(e, 'code', 0), {}

st, f = gh('GET', f'/contents/dane/live/{SHA}.json')
if st != 200: stop(f'plan niedostępny ({st})')
raw = base64.b64decode(f['content'])
if hashlib.sha256(raw).hexdigest() != SHA: stop('sha256 planu ≠ input')
plan = json.loads(raw)
if plan.get('rodzaj') != ROD: stop('rodzaj planu ≠ input')
poz = plan.get('pozycje')
if not isinstance(poz, list) or not 1 <= len(poz) <= 50: stop('liczba pozycji poza 1..50')
if str(len(poz)) != os.environ.get('LICZBA', '').strip(): stop('liczba pozycji ≠ zatwierdzona')
ids = set()
for p in poz:
    if not re.fullmatch(r'\d{9,15}', str(p.get('ItemID', ''))): stop('ItemID niepoprawny')
    if ROD == 'ceny':
        if set(p) - {'ItemID', 'cena_przed', 'cena_po'}: stop('pole spoza allowlisty')
        if not re.fullmatch(r'\d{1,4}\.\d{2}', f"{float(p['cena_po']):.2f}") or float(p['cena_po']) <= 0: stop('cena niepoprawna')
        if p['ItemID'] in ids: stop('duplikat ItemID')
    else:
        if set(p) - {'ItemID', 'TransactionID', 'tekst'}: stop('pole spoza allowlisty')
        if not re.fullmatch(r'\d{1,20}', str(p.get('TransactionID', ''))) or not 0 < len(p.get('tekst', '')) <= 250: stop('notatka niepoprawna')
    ids.add(p['ItemID'])

a = base64.b64encode(f"{os.environ['EBAY_APP_ID']}:{os.environ['EBAY_CERT_ID']}".encode()).decode()
q = r.Request('https://api.ebay.com/identity/v1/oauth2/token',
              u.urlencode({'grant_type': 'refresh_token', 'refresh_token': os.environ['EBAY_REFRESH_TOKEN'],
                           'scope': 'https://api.ebay.com/oauth/api_scope'}).encode(),
              {'Authorization': 'Basic ' + a, 'Content-Type': 'application/x-www-form-urlencoded'})
try:
    TOK = json.load(r.urlopen(q, timeout=60))['access_token']
except Exception as e:
    stop(f'token eBay ({getattr(e, "code", "błąd")})')

def trading(call, inner):
    body = f'<?xml version="1.0" encoding="utf-8"?><{call}Request xmlns="urn:ebay:apis:eBLBaseComponents">{inner}</{call}Request>'
    q = r.Request('https://api.ebay.com/ws/api.dll', body.encode(), {'X-EBAY-API-IAF-TOKEN': TOK, 'X-EBAY-API-CALL-NAME': call,
                  'X-EBAY-API-SITEID': '3', 'X-EBAY-API-COMPATIBILITY-LEVEL': '1193', 'Content-Type': 'text/xml'})
    try:
        return r.urlopen(q, timeout=90).read().decode('utf-8', 'replace')
    except Exception as e:
        return f'<Ack>HTTP{getattr(e, "code", 0)}</Ack>'

wynik = []
if ROD == 'ceny':
    for i in range(0, len(poz), 4):
        part = poz[i:i + 4]
        inner = ''.join(f"<InventoryStatus><ItemID>{p['ItemID']}</ItemID><StartPrice>{float(p['cena_po']):.2f}</StartPrice></InventoryStatus>" for p in part)
        o = trading('ReviseInventoryStatus', inner)
        ack = (re.findall(r'<Ack>(.*?)</Ack>', o) or ['?'])[0]
        for p in part: wynik.append({'ItemID': p['ItemID'], 'cena_po': p['cena_po'], 'ack': ack})
else:
    for p in poz:
        inner = (f"<ItemID>{p['ItemID']}</ItemID><TransactionID>{p['TransactionID']}</TransactionID><Action>AddOrUpdate</Action>"
                 f"<NoteText>{escape(p['tekst'])}</NoteText>")
        o = trading('SetUserNotes', inner)
        wynik.append({'ItemID': p['ItemID'], 'ack': (re.findall(r'<Ack>(.*?)</Ack>', o) or ['?'])[0]})

ok = sum(1 for w in wynik if w['ack'] in ('Success', 'Warning'))
print(f'WYNIK: pozycji {len(poz)}, Ack OK {ok}, błędy {len(poz) - ok}')
sys.exit(0 if ok == len(poz) else 1)
