#!/usr/bin/env python3
"""
SYNC AUTO Systeme.io + Meta -> Supabase (table webinaires) — zero action manuelle.

Remplace wj_auto_sync.py depuis la bascule WebinarJam -> Welya du 09/08/2026.
Tourne via GitHub Actions (.github/workflows/sync.yml).

Ce que ce robot remplit, pour chaque live declare dans lives.json :
  - inscrits_total       = total des contacts portant le tag Systeme.io du live
  - inscrits_ads         = utm_source payant (fb/ig/...) + ManyChat
  - inscrits_organique   = le reste
  - vues / clics / ads_depense  = insights Meta sur la fenetre d'acquisition
Si la fiche du live n'existe pas en base, elle est CREEE.

Ce que ce robot ne peut PAS remplir : tout ce qui vient de Welya (presence,
taux de visionnage, clics sur l'offre, drop-off). L'API Welya /integrations/*
renvoie [] des que le webinaire est termine — il n'existe aucun chemin
serveur-a-serveur. Ces champs arrivent par welya_sync.py, lance a la main
apres chaque live depuis l'export « Exporter les inscrits ».

Champs JAMAIS touches ici : presence (welya_sync.py), ventes/CA (webhook
Systeme.io), clics_lien, notes, objections, dm_prospects.

⚠️ inscrits_total = le TAG Systeme.io, jamais le compteur Welya. Welya ne connait
que les gens qui ont franchi la porte : ses « inscrits » sont des presents, et son
« taux de participation » de 97 % n'est pas un show-up. Le vrai show-up se lit
presents_pic / inscrits_total.

Usage local :
  SYSTEME_API_KEY=... META_TOKEN=... python3 welya_auto_sync.py [--since AAAA-MM-JJ]
  --since = rejoue tous les lives depuis cette date, sinon fenetre -10 j / +10 j.
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

SYSTEME_API_KEY = os.environ.get("SYSTEME_API_KEY", "").strip()
META_TOKEN = os.environ.get("META_TOKEN", "").strip()
META_ACCOUNT = os.environ.get("META_ACCOUNT", "3739233859731846")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://mxnrqnpvcxwdwykzzchk.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_f_MKNNQu-CG7LqW3Bs0sUA_TIU2fBUf")

UA = "curl/8.4.0"
# utm_source considere comme payant. 'an' = Audience Network.
PAID = {"fb", "ig", "facebook", "instagram", "meta", "an"}

if not SYSTEME_API_KEY:
    sys.exit("SYSTEME_API_KEY manquant (variable d'environnement)")

LIVES_PATH = Path(__file__).resolve().parent / "lives.json"
LIVES = {k: v for k, v in json.loads(LIVES_PATH.read_text(encoding="utf-8")).items()
         if not k.startswith("_")}


def sio(path, **params):
    url = "https://api.systeme.io/api/" + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers={"X-API-Key": SYSTEME_API_KEY, "User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read().decode())


def sb(method, path, body=None, prefer=None):
    headers = {"apikey": SUPABASE_KEY, "Authorization": "Bearer " + SUPABASE_KEY,
               "Content-Type": "application/json", "User-Agent": UA}
    if prefer:
        headers["Prefer"] = prefer
    req = urllib.request.Request(SUPABASE_URL + path,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=60) as r:
        txt = r.read().decode()
        return json.loads(txt) if txt else None


def contacts_du_tag(tag):
    """Tous les contacts portant le tag. C'est la base d'inscription REELLE du live."""
    out, after = [], None
    while True:
        p = {"tags": tag, "limit": 100}
        if after:
            p["startingAfter"] = after
        d = sio("contacts", **p)
        items = d.get("items", [])
        out += items
        if not d.get("hasMore") or not items:
            break
        after = items[-1]["id"]
        if len(out) > 20000:          # garde-fou anti-boucle
            break
    return out


def utm_of(c):
    """1) champs personnalises utm_*  2) query string du sourceURL. Repris de cpl_2aout.py."""
    out = {}
    for f in c.get("fields", []):
        s = (f.get("slug") or "").lower()
        if s.startswith("utm_") and f.get("value"):
            out[s] = f["value"]
    if out:
        return out
    su = c.get("sourceURL") or ""
    if "utm_" in su:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(su).query)
        return {k: v[0] for k, v in q.items() if k.startswith("utm_")}
    return {}


def ventile(cts):
    """inscrits_total / ads / organique. ManyChat compte comme acquisition payante."""
    ads = 0
    for c in cts:
        u = utm_of(c)
        src = (u.get("utm_source") or "").lower()
        med = (u.get("utm_medium") or "").lower()
        if src in PAID or "manychat" in med:
            ads += 1
    return {"inscrits_total": len(cts),
            "inscrits_ads": ads,
            "inscrits_organique": max(0, len(cts) - ads)}


def meta_insights(since, until, campagne_id=None):
    """Spend/vues/clics. Au niveau campagne si l'id est connu, sinon au niveau compte."""
    noeud = campagne_id if campagne_id else ("act_%s" % META_ACCOUNT)
    qs = urllib.parse.urlencode({
        "fields": "spend,impressions,actions",
        "time_range": json.dumps({"since": since, "until": until}),
        "access_token": META_TOKEN,
    })
    req = urllib.request.Request("https://graph.facebook.com/v25.0/%s/insights?%s" % (noeud, qs),
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.loads(r.read().decode()).get("data", [])
    if not rows:
        return None
    d0 = rows[0]
    clics = 0
    for a in (d0.get("actions") or []):
        if a.get("action_type") == "link_click":
            clics = int(float(a.get("value", 0)))
    return {"vues": int(float(d0.get("impressions", 0))),
            "clics": clics,
            "ads_depense": round(float(d0.get("spend", 0)))}


# --- fenetre de dates ---
today = datetime.date.today()
since_arg = None
if "--since" in sys.argv:
    since_arg = sys.argv[sys.argv.index("--since") + 1]
lo = since_arg or (today - datetime.timedelta(days=10)).isoformat()
hi = (today + datetime.timedelta(days=10)).isoformat()

cibles = sorted([(d, v) for d, v in LIVES.items() if lo <= d <= hi])
print("Sync Systeme.io+Meta -> Supabase | fenetre %s -> %s | %d live(s)" % (lo, hi, len(cibles)))
if not cibles:
    print("Aucun live declare sur la fenetre. Penser a ajouter l'entree dans lives.json.")

for d, cfg in cibles:
    try:
        cts = contacts_du_tag(cfg["tag"])
    except Exception as e:
        print("  %s : Systeme.io KO (%s)" % (d, str(e)[:120]))
        continue
    if not cts:
        print("  %s : 0 contact sur le tag %s, skip" % (d, cfg["tag"]))
        continue
    patch = ventile(cts)

    rows = sb("GET", "/rest/v1/webinaires?date=eq.%s&select=id" % d) or []
    if rows:
        sb_id = rows[0]["id"]
        sb("PATCH", "/rest/v1/webinaires?id=eq.%s" % sb_id, patch, prefer="return=minimal")
        action = "maj"
    else:
        created = sb("POST", "/rest/v1/webinaires",
                     {"date": d, "heure": "18:00",
                      "titre": cfg.get("titre") or ("Webinaire %s" % d),
                      "type": "atelier", **patch}, prefer="return=representation") or []
        sb_id = created[0]["id"] if created else None
        action = "CREE"
    print("  %s [%s] : %d inscrits (%d ads / %d organique)" % (
        d, action, patch["inscrits_total"], patch["inscrits_ads"], patch["inscrits_organique"]))

    # --- Meta : fenetre d'acquisition = lendemain du live precedent -> jour du live ---
    # On ne se sert PAS de 'debut' ici : ce champ est la fenetre de reference du CPL,
    # amputee des journees polluees. Pour le dashboard, la depense affichee doit couvrir
    # tout ce qui a ete paye pour remplir ce live, sinon le ROAS est flatte.
    if not META_TOKEN:
        continue
    precedents = [x for x in sorted(LIVES) if x < d]
    if precedents:
        debut = (datetime.date.fromisoformat(precedents[-1]) + datetime.timedelta(days=1)).isoformat()
    else:
        debut = cfg.get("debut") or (datetime.date.fromisoformat(d) - datetime.timedelta(days=6)).isoformat()
    try:
        m = meta_insights(debut, d, cfg.get("id"))
        if m and m["ads_depense"] > 0:
            sb("PATCH", "/rest/v1/webinaires?date=eq.%s" % d, m, prefer="return=minimal")
            cpl = m["ads_depense"] / patch["inscrits_total"]
            print("     Meta %s->%s | vues %d | clics %d | spend %d EUR | CPL %.2f EUR" % (
                debut, d, m["vues"], m["clics"], m["ads_depense"], cpl))
    except Exception as e:
        print("     Meta skip (%s)" % str(e)[:120])

if not META_TOKEN:
    print("Meta : META_TOKEN absent -> vues/clics/budget non synchronises")
print("DONE")
