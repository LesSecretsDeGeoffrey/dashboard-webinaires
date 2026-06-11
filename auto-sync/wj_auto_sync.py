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
META_TOKEN = os.environ.get("META_TOKEN", "").strip()  # optionnel : si absent, la partie Meta est sautée
META_ACCOUNT = os.environ.get("META_ACCOUNT", "3739233859731846")
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


def signup_date(r):
    """'Sun, 7 Jun 2026, 08:16 PM' -> datetime.date (None si invalide)."""
    try:
        d = str(r.get("signup_date", "")).split(", ")[1].split(" ")
        return datetime.date(int(d[2]), int(MOIS.get(d[1][:3], "01")), int(d[0]))
    except Exception:
        return None


def age_bucket(age_days):
    """Anciennete d'inscription -> tranche (les inscrits du jour J se pointent ~2x plus)."""
    if age_days <= 0:
        return 0   # jour J
    if age_days == 1:
        return 1   # veille
    if age_days <= 3:
        return 2
    if age_days <= 6:
        return 3
    return 4       # J-7 et plus


def showup_rates_par_anciennete(regs, live_date):
    """Taux de show-up REEL par tranche d'anciennete, mesure sur un live passe."""
    tot, att = {}, {}
    for r in regs:
        sd = signup_date(r)
        if not sd:
            continue
        b = age_bucket((live_date - sd).days)
        tot[b] = tot.get(b, 0) + 1
        if str(r.get("attended_live")) == "Yes":
            att[b] = att.get(b, 0) + 1
    glob = sum(att.values()) / sum(tot.values()) if tot else 0
    return {b: (att.get(b, 0) / tot[b]) if tot[b] >= 10 else glob for b in tot}, glob


def predit_showup(regs, live_date, rates, glob):
    """Show-up previsionnel (%) du prochain live selon le mix d'anciennete de SES inscrits."""
    if not regs:
        return 0
    pred = 0.0
    for r in regs:
        sd = signup_date(r)
        b = age_bucket((live_date - sd).days) if sd else 4
        pred += rates.get(b, glob)
    return max(0, min(100, round(100 * pred / len(regs))))


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

processed = []  # (date_iso, regs, st, sb_id)
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
        sb_id = rows[0]["id"]
        sb("PATCH", "/rest/v1/webinaires?id=eq.%s" % sb_id, patch, prefer="return=minimal")
        action = "maj"
    else:
        created = sb("POST", "/rest/v1/webinaires",
                     {"date": d, "heure": "18:00", "titre": w.get("name") or ("Webinaire %s" % d),
                      "type": "atelier", **patch}, prefer="return=representation") or []
        sb_id = created[0]["id"] if created else None
        action = "CREE"
    processed.append((d, regs, st, sb_id))
    print("  %s (WJ %s) [%s] : %d ins (%d ads) | debut %d | pic %d | pitch %d | tx %d%%" % (
        d, wid, action, st["inscrits_total"], st["inscrits_ads"],
        st["presents_debut"], st["presents_pic"], st["presents_pitch"], st["tx_visionnage_live"]))

# --- SHOW-UP PREVISIONNEL des lives a venir ---
# Calibre sur le DERNIER live passe (taux de show-up reel par anciennete d'inscription),
# applique au mix d'anciennete des inscrits du prochain live. PATCH separe et garde
# (try/except) : si la colonne showup_previsionnel n'existe pas encore, le sync
# principal n'est PAS affecte.
passes = [(d, regs) for d, regs, st, _ in processed if st["presents_pic"] > 0]
futurs = [(d, regs) for d, regs, st, _ in processed if st["presents_pic"] == 0
          and d >= today.isoformat()]
if passes and futurs:
    ref_d, ref_regs = passes[-1]
    rates, glob = showup_rates_par_anciennete(ref_regs, datetime.date.fromisoformat(ref_d))
    for d, regs in futurs:
        pct = predit_showup(regs, datetime.date.fromisoformat(d), rates, glob)
        if pct <= 0:
            continue
        try:
            sb("PATCH", "/rest/v1/webinaires?date=eq.%s" % d,
               {"showup_previsionnel": pct}, prefer="return=minimal")
            print("  %s : show-up previsionnel %d%% (calibre sur %s)" % (d, pct, ref_d))
        except Exception as e:
            print("  %s : prevision %d%% NON ecrite (colonne manquante ? %s)" % (d, pct, e))

# --- PUB META (vues / clics / budget) — uniquement si un jeton est fourni ---
# Fenêtre d'acquisition d'un live = lendemain du live précédent -> jour du live
# (la méthode validée à la main : spend compte entier sur la fenêtre ; suppose
# que toutes les campagnes actives servent le live en cours, ce qui est le cas).
def meta_insights(since, until):
    qs = urllib.parse.urlencode({
        "fields": "spend,impressions,actions",
        "time_range": json.dumps({"since": since, "until": until}),
        "access_token": META_TOKEN,
    })
    req = urllib.request.Request(
        "https://graph.facebook.com/v25.0/act_%s/insights?%s" % (META_ACCOUNT, qs),
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


if META_TOKEN and processed:
    try:
        toutes = [r["date"] for r in (sb("GET", "/rest/v1/webinaires?select=date&order=date.asc") or [])]
    except Exception:
        toutes = []
    for d, regs, st, _ in processed:
        avant = [x for x in toutes if x < d]
        since = (datetime.date.fromisoformat(avant[-1]) + datetime.timedelta(days=1)).isoformat() \
            if avant else (datetime.date.fromisoformat(d) - datetime.timedelta(days=6)).isoformat()
        try:
            m = meta_insights(since, d)
            if m and m["ads_depense"] > 0:
                sb("PATCH", "/rest/v1/webinaires?date=eq.%s" % d, m, prefer="return=minimal")
                print("  %s : Meta %s->%s | vues %d | clics %d | spend %d EUR" % (
                    d, since, d, m["vues"], m["clics"], m["ads_depense"]))
        except Exception as e:
            print("  %s : Meta skip (%s)" % (d, str(e)[:120]))
else:
    print("Meta : META_TOKEN absent -> vues/clics/budget non synchronises (optionnel)")

# --- DROP-OFF minute par minute + HESITANTS CHAUDS -> DM Tracking ---
PRICE_MIN = int(os.environ.get("PRICE_MIN", "100"))  # minute ou le prix est annonce
SYSTEME_API_KEY = os.environ.get("SYSTEME_API_KEY", "").strip()


def acheteurs_connus():
    """Emails a NE JAMAIS DM : ventes Methode (table ventes) + tag acheteur Systeme.io."""
    emails = set()
    try:
        for v in (sb("GET", "/rest/v1/ventes?select=email,montant") or []):
            if v.get("email") and (v.get("montant") or 0) >= 100:
                emails.add(str(v["email"]).lower())
    except Exception:
        pass
    if SYSTEME_API_KEY:
        try:
            after = None
            for _ in range(60):
                q = "https://api.systeme.io/api/contacts?limit=100&tags=1850371" + (
                    ("&startingAfter=%s" % after) if after else "")
                req = urllib.request.Request(q, headers={"X-API-Key": SYSTEME_API_KEY, "User-Agent": UA})
                with urllib.request.urlopen(req, timeout=60) as r:
                    dd = json.loads(r.read().decode())
                items = dd.get("items", [])
                for c in items:
                    if c.get("email"):
                        emails.add(str(c["email"]).lower())
                if not dd.get("hasMore") or not items:
                    break
                after = items[-1]["id"]
        except Exception as e:
            print("Exclusion Systeme.io : skip (%s)" % str(e)[:80])
    return emails


def e164(r):
    cc = "".join(ch for ch in str(r.get("phone_country_code") or "") if ch.isdigit())
    num = "".join(ch for ch in str(r.get("phone_number") or "") if ch.isdigit())
    if not num:
        return None
    return ("+" + cc + num.lstrip("0")) if cc else None


exclus = None
for d, regs, st, sb_id in processed:
    if not sb_id or st["presents_pic"] <= 0:
        continue
    att = [r for r in regs if str(r.get("attended_live")) == "Yes"]

    # 1) Courbe de presence minute par minute (page Drop-off du dashboard)
    try:
        fins = sorted(dur(r.get("entered_live")) + dur(r.get("time_live")) for r in att)
        fin_min = min(240, int(fins[max(0, int(len(fins) * 0.98) - 1)] / 60) + 5)
        pts = []
        for m in range(0, fin_min + 1):
            t = m * 60
            pts.append(sum(1 for r in att
                           if dur(r.get("entered_live")) <= t <= dur(r.get("entered_live")) + dur(r.get("time_live"))))
        pic = max(pts) or 1
        rows_out = []
        for m, n in enumerate(pts):
            delta = (pts[m - 1] - n) if m > 0 else 0
            rows_out.append({"webinaire_id": sb_id, "minute": m, "presents_count": n,
                             "drop_pct": round((pic - n) / pic * 100, 2),
                             "is_drop_critical": bool(m > 0 and delta >= max(3, pic * 0.03))})
        sb("DELETE", "/rest/v1/dropoff_points?webinaire_id=eq.%s" % sb_id)
        for i in range(0, len(rows_out), 200):
            sb("POST", "/rest/v1/dropoff_points", rows_out[i:i + 200], prefer="return=minimal")
        print("  %s : drop-off ecrit (%d minutes, pic %d)" % (d, len(rows_out), pic))
    except Exception as e:
        print("  %s : drop-off skip (%s)" % (d, str(e)[:100]))

    # 2) Hesitants chauds -> DM Tracking (lives de moins de 8 jours, une seule fois)
    if (today - datetime.date.fromisoformat(d)).days > 8:
        continue
    try:
        deja = sb("GET", "/rest/v1/dm_prospects?webinaire_id=eq.%s&source=eq.auto&select=id&limit=1" % sb_id) or []
        if deja:
            continue
        if exclus is None:
            exclus = acheteurs_connus()
            print("Exclusion DM : %d acheteurs connus" % len(exclus))
        chauds = []
        for r in att:
            em = (r.get("email") or "").lower().strip()
            ent, tl = dur(r.get("entered_live")), dur(r.get("time_live"))
            if not em or em in exclus or str(r.get("purchased_live")) == "Yes":
                continue
            if not (ent <= PRICE_MIN * 60 <= ent + tl):  # present au moment du prix
                continue
            chauds.append((tl, r))
        chauds.sort(key=lambda x: -x[0])
        rows_dm = []
        for tl, r in chauds[:40]:
            h, mn = int(tl // 3600), int((tl % 3600) // 60)
            rows_dm.append({"webinaire_id": sb_id,
                            "prenom": (r.get("first_name") or "?")[:60],
                            "email": (r.get("email") or "").lower(),
                            "telephone": e164(r) or "",
                            "time_live_min": int(tl / 60),
                            "source": "auto", "status": "a_envoyer",
                            "notes": "[auto] reste %dh%02d, present au prix (min %d)" % (h, mn, PRICE_MIN)})
        if rows_dm:
            sb("POST", "/rest/v1/dm_prospects", rows_dm, prefer="return=minimal")
            print("  %s : %d hesitants chauds -> DM Tracking" % (d, len(rows_dm)))
    except Exception as e:
        print("  %s : hesitants skip (migration-dm.sql lancee ? %s)" % (d, str(e)[:100]))

print("DONE")
