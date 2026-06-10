#!/usr/bin/env python3
"""
SYNC AUTO WebinarJam -> Supabase (table webinaires) — zéro action manuelle.

Tourne via GitHub Actions (.github/workflows/wj-sync.yml, 4x/jour) :
pour chaque webinaire WJ dont le live est à ±10 jours, calcule depuis les
inscrits WJ et met à jour la base du dashboard :
  - inscrits_total / inscrits_ads (utm payant + ManyChat) / inscrits_organique
  - presents_pic        = attended_live == "Yes"
  - presents_pitch      = présents encore là à PITCH_START_MIN (defaut 70 min)
  - presents_debut      = présents pendant les 5 premières minutes
  - tx_visionnage_live  = moyenne(time_live des présents) / durée du live (%)
Si la fiche du live n'existe pas en base, elle est CRÉÉE (le webhook ventes
peut alors attribuer ses ventes même si personne n'a rempli le formulaire).

Champs JAMAIS touchés ici : ventes/CA (webhook Systeme.io), vues/clics/budget
(Meta), clics_lien (manuel), notes, objections, dm_prospects.

Usage local : WJ_API_KEY=... python3 wj_auto_sync.py [--since YYYY-MM-DD]
  --since = backfill (tous les lives depuis cette date), sinon fenêtre ±10 j.
"""
import json
import os
import sys
import datetime
import urllib.request
import urllib.parse

WJ_API_KEY = os.environ.get("WJ_API_KEY", "").strip()
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://mxnrqnpvcxwdwykzzchk.supabase.co")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "sb_publishable_f_MKNNQu-CG7LqW3Bs0sUA_TIU2fBUf")
PITCH_START_MIN = int(os.environ.get("PITCH_START_MIN", "70"))
DEBUT_MIN = 5            # « présence début » = encore là à la minute 5
PAID = {"fb", "ig", "facebook", "instagram", "meta", "an"}
MOIS = {"Jan": "01", "Feb": "02", "Mar": "03", "Apr": "04", "May": "05", "Jun": "06",
        "Jul": "07", "Aug": "08", "Sep": "09", "Oct": "10", "Nov": "11", "Dec": "12"}

if not WJ_API_KEY:
    sys.exit("WJ_API_KEY manquant (variable d'environnement)")


UA = "curl/8.4.0"  # l'API WJ (WAF) bloque le User-Agent urllib par défaut


def wj(ep, **params):
    data = urllib.parse.urlencode({"api_key": WJ_API_KEY, **params}).encode()
    req = urllib.request.Request("https://api.webinarjam.com/webinarjam/" + ep, data=data,
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=90) as r:
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


def wj_date(w):
    """'Sun, 7 Jun 2026, 06:00 PM' -> '2026-06-07' (premier schedule valide)."""
    for s in (w.get("schedules") or []):
        try:
            d = str(s).split(", ")[1].split(" ")
            return "%s-%s-%s" % (d[2], MOIS.get(d[1][:3], "00"), d[0].zfill(2))
        except Exception:
            pass
    return None


def dur(s):
    """'02:16:42' -> secondes (0 si vide/invalide)."""
    try:
        h, m, sec = str(s or "00:00:00").split(":")
        return int(h) * 3600 + int(m) * 60 + int(sec)
    except Exception:
        return 0


def registrants(wid):
    out, page = [], 1
    while True:
        b = wj("registrants", webinar_id=wid, page=page).get("registrants", {})
        out.extend(b.get("data", []))
        if page >= b.get("last_page", 1):
            break
        page += 1
    return out


def stats(regs):
    ads = [r for r in regs if (r.get("utm_source") or "").lower() in PAID]
    mc = [r for r in regs if (r.get("utm_source") or "").lower() == "post"
          and "manychat" in (r.get("utm_medium") or "").lower()]
    att = [r for r in regs if str(r.get("attended_live")) == "Yes"]
    fins = sorted(dur(r.get("entered_live")) + dur(r.get("time_live")) for r in att)
    pitch = [r for r in att if dur(r.get("entered_live")) + dur(r.get("time_live")) >= PITCH_START_MIN * 60]
    debut = [r for r in att if dur(r.get("entered_live")) <= DEBUT_MIN * 60
             and dur(r.get("entered_live")) + dur(r.get("time_live")) >= DEBUT_MIN * 60]
    tx = 0
    if fins:
        fin_live = fins[max(0, int(len(fins) * 0.98) - 1)]  # ~fin du live (98e pct, robuste aux outliers)
        if fin_live >= 30 * 60:  # pas de taux fiable sur un live < 30 min de données
            moy = sum(dur(r.get("time_live")) for r in att) / len(att)
            tx = max(0, min(100, round(100 * moy / fin_live)))
    n_ads = len(ads) + len(mc)
    return {
        "inscrits_total": len(regs),
        "inscrits_ads": n_ads,
        "inscrits_organique": max(0, len(regs) - n_ads),
        "presents_pic": len(att),
        "presents_pitch": len(pitch),
        "presents_debut": len(debut),
        "tx_visionnage_live": tx,
    }


# --- fenêtre de dates ---
today = datetime.date.today()
since = None
if len(sys.argv) > 2 and sys.argv[1] == "--since":
    since = sys.argv[2]
lo = since or (today - datetime.timedelta(days=10)).isoformat()
hi = (today + datetime.timedelta(days=10)).isoformat()

webs = [(wj_date(w), w) for w in wj("webinars").get("webinars", [])]
targets = sorted([(d, w) for d, w in webs if d and lo <= d <= hi])
print("Sync WJ -> Supabase | fenetre %s -> %s | %d live(s)" % (lo, hi, len(targets)))

for d, w in targets:
    wid = w["webinar_id"]
    regs = registrants(wid)
    if not regs:
        print("  %s (WJ %s) : 0 inscrit, skip" % (d, wid))
        continue
    st = stats(regs)
    # Jamais écraser de la présence déjà en base par du 0 (live pas encore passé)
    patch = {"inscrits_total": st["inscrits_total"], "inscrits_ads": st["inscrits_ads"],
             "inscrits_organique": st["inscrits_organique"]}
    for k in ("presents_pic", "presents_pitch", "presents_debut", "tx_visionnage_live"):
        if st[k] > 0:
            patch[k] = st[k]
    rows = sb("GET", "/rest/v1/webinaires?date=eq.%s&select=id" % d) or []
    if rows:
        sb("PATCH", "/rest/v1/webinaires?id=eq.%s" % rows[0]["id"], patch, prefer="return=minimal")
        action = "maj"
    else:
        sb("POST", "/rest/v1/webinaires",
           {"date": d, "heure": "18:00", "titre": w.get("name") or ("Webinaire %s" % d),
            "type": "atelier", **patch}, prefer="return=minimal")
        action = "CREE"
    print("  %s (WJ %s) [%s] : %d ins (%d ads) | debut %d | pic %d | pitch %d | tx %d%%" % (
        d, wid, action, st["inscrits_total"], st["inscrits_ads"],
        st["presents_debut"], st["presents_pic"], st["presents_pitch"], st["tx_visionnage_live"]))

print("DONE")
