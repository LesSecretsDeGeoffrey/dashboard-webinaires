#!/usr/bin/env python3
"""
ATTRIBUTION DES VENTES (phase 1) : quelle pub / quel canal ramene chaque vente.

Trois passes, toutes idempotentes (upsert par cle, relançable a volonte) :
  1. Meta insights level=ad, J-3 -> J    -> depenses_ads   (spend par pub/jour)
  2. Contacts Systeme.io (3 passes)      -> contacts_sio   (utm de l'inscription)
  3. Ventes sans attribution             -> attribution    (+ touche 'achat')

En phase 1, la table touches est vide : le modele retombe toujours sur
'sio_contact' (champs utm du contact) — c'est voulu, et ca couvre tout
l'historique. Le modele complet par touches est deja code et teste pour la
phase 2 (snippet + Cloudflare Pages).

La definition du canal (canal_de) existe AUSSI en SQL dans
attribution-schema.sql : toute modification se fait aux DEUX endroits
(test de parite dans test_attribution_sync.py).

Usage :
  python3 attribution_sync.py                # tout (depenses + contacts + attribution)
  python3 attribution_sync.py --dry-run      # calcule et affiche, n'ecrit rien
  python3 attribution_sync.py --recalc       # recalcule TOUTES les attributions
  python3 attribution_sync.py --skip-meta    # sans l'etape Meta (pas de META_TOKEN)

Env : SYSTEME_API_KEY, META_TOKEN, SUPABASE_SERVICE_KEY (jamais la publishable :
le robot ecrit dans des tables protegees par RLS).
"""
import datetime
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
import hashlib
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from empreinte import empreinte  # noqa: E402  (copie de ads-atelier-macarons-2aout/empreinte.py, test de parite)

SYSTEME_API_KEY = os.environ.get("SYSTEME_API_KEY", "").strip()
META_TOKEN = os.environ.get("META_TOKEN", "").strip()
META_ACCOUNT = os.environ.get("META_ACCOUNT", "3739233859731846")
SUPABASE_URL = os.environ.get("SUPABASE_URL", "https://mxnrqnpvcxwdwykzzchk.supabase.co")
SERVICE_KEY = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()

UA = "curl/8.4.0"
HERE = Path(__file__).resolve().parent
SCHEMA_PATH = HERE.parent / "attribution-schema.sql"
LIVES_PATH = HERE / "lives.json"

# Copie stricte de canal_de() du schema SQL (test de parite).
# ManyChat n'est PAS payant ici (contrairement a welya_auto_sync.ventile,
# qui melange ManyChat aux ads pour la colonne inscrits_ads du dashboard).
PAID = {"fb", "ig", "facebook", "instagram", "an", "msg", "meta", "ads"}
CANAUX = {"manychat", "email", "story", "bio", "whatsapp"}
FENETRE_J = 90
# Namespace fixe pour les ids deterministes des touches 'achat' (uuid5).
NS_ACHAT = uuid.uuid5(uuid.NAMESPACE_DNS, "achat.lessecretsdegeoffrey.fr")

TUNNELS_PATH = [
    ("/paiementfondationspro", "live"),
    ("/paiementbook", "ebook"),
    ("/maitrise", "ebook"),
    ("/paiementmethode997", "call"),
]
TUNNELS_PRODUIT = [
    # '997' AVANT 'fondations' : le produit 997 s'appelle sans doute aussi
    # 'Methode Fondations ...' — verifier le libelle reel au dry-run (Task 8)
    ("997", "call"),
    ("fondations", "live"), ("mfp", "live"),
    ("ebook", "ebook"), ("90 jours", "ebook"), ("book", "ebook"),
    ("macaron", "lowticket"),
]


def canal_de(src, med):
    s = (src or "").lower().strip()
    m = (med or "").lower().strip()
    if m == "paid" or s in PAID:
        return "ads"
    if s in CANAUX:
        return s
    if "manychat" in m:
        # donnees reelles : ManyChat vit dans le MEDIUM (utm_medium=manychat-insta,
        # utm_source=post) — vu sur les acheteurs du 09/08 au controle de reference
        return "manychat"
    if m == "lien":
        return "lien"
    return "organique"


def est_payante(t):
    return canal_de(t.get("utm_source"), t.get("utm_medium")) == "ads"


def porte_une_source(t):
    """Porte une source declaree (utm_source/utm_medium ou lien court), que
    canal_de sache la classer ou non. Sans ce filtre, canal_dernier
    degenererait en 'organique' sur quasi toutes les ventes de l'ere snippet
    (landing avec UTM -> pages internes -> checkout, generalement sans UTM)."""
    return bool(t.get("utm_source") or t.get("utm_medium") or t.get("slug"))


def tunnel_de(path, produit):
    p = (path or "").lower()
    for frag, t in TUNNELS_PATH:
        if frag in p:
            return t
    pr = (produit or "").lower()
    for frag, t in TUNNELS_PRODUIT:
        if frag in pr:
            return t
    return "autre"


def ligne_depense(r):
    """Ligne d'insights Meta (level=ad, time_increment=1) -> ligne depenses_ads."""
    nom = r.get("ad_name") or ""
    return {
        "date": r.get("date_start"),
        "ad_id": str(r.get("ad_id") or ""),
        "campaign_id": r.get("campaign_id"), "campaign_name": r.get("campaign_name"),
        "adset_id": r.get("adset_id"), "adset_name": r.get("adset_name"),
        "ad_name": nom,
        "slug_crea": nom.split(" | ")[0].strip(),
        "spend": float(r.get("spend") or 0),
        "impressions": int(float(r.get("impressions") or 0)),
        "clicks": int(float(r.get("clicks") or 0)),
    }


def utm_of(c):
    """Champs personnalises utm_* du contact, sinon query du sourceURL.
    Meme logique que welya_auto_sync.utm_of / cpl_2aout."""
    out = {}
    for f in c.get("fields", []):
        s = (f.get("slug") or "").lower()
        if (s.startswith("utm_") or s in ("fbclid", "fbc")) and f.get("value"):
            out[s] = f["value"]
    if any(k.startswith("utm_") for k in out):
        return out
    su = c.get("sourceURL") or ""
    if "utm_" in su:
        q = urllib.parse.parse_qs(urllib.parse.urlparse(su).query)
        out.update({k: v[0] for k, v in q.items() if k.startswith("utm_")})
    return out


def ligne_contact(c):
    u = utm_of(c)
    return {
        "contact_id": str(c.get("id")),
        "email": (c.get("email") or "").strip().lower() or None,
        "registered_at": c.get("registeredAt"),
        "utm_source": u.get("utm_source"), "utm_medium": u.get("utm_medium"),
        "utm_campaign": u.get("utm_campaign"), "utm_term": u.get("utm_term"),
        "utm_content": u.get("utm_content"),
        "fbclid": u.get("fbclid"), "fbc": u.get("fbc"),
        "tags": [str(t.get("id")) for t in (c.get("tags") or [])],
        "synced_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }


def _contact_jsonb(t):
    return {k: t.get(k) for k in ("ts", "type", "path", "utm_source", "utm_medium",
                                  "utm_campaign", "utm_term", "utm_content", "utm_id", "slug")}


def _iso(ts):
    """Postgres tronque les zeros finaux de la fraction de seconde ; py<3.11
    exige que fromisoformat recoive 3 ou 6 chiffres. On pad a 6."""
    s = str(ts).replace("Z", "+00:00")
    s = re.sub(r"\.(\d{1,6})", lambda m: "." + m.group(1).ljust(6, "0"), s)
    return datetime.datetime.fromisoformat(s)


def attribuer(vente, touches, contact):
    """vente + touches de la personne + contact SIO -> ligne attribution.
    PURE : aucune I/O. touches = liste brute, filtrage fenetre/achat ici."""
    achat_ts = _iso(vente["purchased_at"])
    debut = achat_ts - datetime.timedelta(days=FENETRE_J)
    utiles = sorted(
        (t for t in touches
         if t.get("type") != "achat" and debut <= _iso(t["ts"]) <= achat_ts),
        key=lambda t: _iso(t["ts"]))

    row = {
        "vente_id": vente["id"],
        "email": (vente.get("email") or "").strip().lower() or None,
        "vid": None, "modele": "aucune",
        "premier_contact": None, "dernier_contact": None, "dernier_contact_payant": None,
        "canal": None, "canal_dernier": None, "tunnel": None,
        "ad_id": None, "slug_crea": None, "adset_name": None, "campaign_name": None,
        "nb_touches": len(utiles), "delai_j": None,
        "calcule_le": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }

    if utiles:
        premier, dernier = utiles[0], utiles[-1]
        # "Dernier contact" (canal_dernier, ecran Canaux) = la derniere touche
        # PORTANT une source, pas forcement utiles[-1] : les pages internes et
        # l'identite au checkout n'ont EN GENERAL pas d'UTM (le snippet relaie
        # les UTM de l'URL de checkout quand ils y sont, voir porte_une_source).
        # Repli sur utiles[-1] pour une visite 100% directe (organique legitime).
        avec_source = [t for t in utiles if porte_une_source(t)]
        dernier_source = avec_source[-1] if avec_source else dernier
        payantes = [t for t in utiles if est_payante(t)]
        retenue = payantes[-1] if payantes else premier
        row.update({
            "modele": "last_paid" if payantes else "first",
            "vid": retenue.get("vid") or dernier.get("vid"),
            "premier_contact": _contact_jsonb(premier),
            "dernier_contact": _contact_jsonb(dernier_source),
            "dernier_contact_payant": _contact_jsonb(payantes[-1]) if payantes else None,
            "canal": canal_de(retenue.get("utm_source"), retenue.get("utm_medium")),
            "canal_dernier": canal_de(dernier_source.get("utm_source"), dernier_source.get("utm_medium")),
            "ad_id": retenue.get("utm_id"),
            "slug_crea": retenue.get("utm_content"),
            "adset_name": retenue.get("utm_term"),
            "tunnel": tunnel_de(dernier.get("path"), vente.get("produit")),
            "delai_j": round((achat_ts - _iso(premier["ts"])).total_seconds() / 86400, 1),
        })
    elif contact is not None:
        u = utm_of(contact)
        canal = canal_de(u.get("utm_source"), u.get("utm_medium"))
        row.update({
            "modele": "sio_contact",
            "premier_contact": u or None, "dernier_contact": u or None,
            "canal": canal, "canal_dernier": canal,
            "slug_crea": u.get("utm_content"), "adset_name": u.get("utm_term"),
            "tunnel": tunnel_de(None, vente.get("produit")),
        })
    else:
        row["tunnel"] = tunnel_de(None, vente.get("produit"))
    return row


def achat_touche_id(vente_id):
    return str(uuid.uuid5(NS_ACHAT, str(vente_id)))


# ---------------------------------------------------------------- Purchase CAPI (phase 3)

PIXEL = os.environ.get("META_PIXEL", "2085581652276222")
CAPI_FENETRE_J = 7        # Meta refuse un event_time de plus de 7 jours
CAPI_DOUBLON_J = 120      # fenetre du garde-fou "meme email + meme produit deja envoye"
# event_source_url quand ventes.raw.page n'est pas une URL (tous les achats d'avant le webhook)
TUNNELS_URL = {
    "live": "https://www.lessecretsdegeoffrey.fr/paiementfondationspro",
    "ebook": "https://www.lessecretsdegeoffrey.fr/paiementbook",
    "call": "https://www.lessecretsdegeoffrey.fr/paiementmethode997",
}
URL_DEFAUT = "https://www.lessecretsdegeoffrey.fr/"


def sha(v):
    """Meta veut les donnees personnelles hachees, minuscules, sans espaces
    (meme normalisation que capi_lead.py et que l'optin v9)."""
    return hashlib.sha256(str(v).strip().lower().encode("utf-8")).hexdigest()


def eid_purchase(email, vente_id):
    """event_id du Purchase : 'purchase-' + empreinte(email|vente_id) (spec §7.4).
    Deterministe -> relancable ; le pixel n'envoie plus de Purchase (spec §9),
    il n'y a donc aucun doublon navigateur a fusionner, seulement la
    deduplication Meta 48 h si le robot rejoue."""
    return "purchase-" + empreinte(email.strip().lower() + "|" + str(vente_id))


def _derniere_touche_avec(touches, cle, jusqua):
    """Derniere touche (hors 'achat', fabriquee par le robot sans IP) portant la cle,
    parmi celles datees au plus tard a l'achat (jusqua) : une touche posterieure
    a l'achat n'est pas une cause de l'achat (pageview du lendemain avant le
    passage du cron). Departage a egalite de ts comme attribuer() : la DERNIERE
    de la liste gagne (touches_de trie deja par ts,id)."""
    cand = [t for t in touches
            if t.get("type") != "achat" and t.get(cle) and _iso(t["ts"]) <= jusqua]
    return sorted(cand, key=lambda t: _iso(t["ts"]))[-1] if cand else None


def _fbc_de(touches, premier_contact, ts, jusqua):
    t = _derniere_touche_avec(touches, "fbc", jusqua)
    if t:
        return t["fbc"]
    t = _derniere_touche_avec(touches, "fbclid", jusqua)
    if t:
        return "fb.1.%d.%s" % (int(_iso(t["ts"]).timestamp() * 1000), t["fbclid"])
    pc = premier_contact or {}
    if pc.get("fbc"):
        return pc["fbc"]
    if pc.get("fbclid"):   # format attendu par Meta : fb.1.<horodatage ms>.<fbclid>
        return "fb.1.%d.%s" % (ts * 1000, pc["fbclid"])
    return None


def evenement_purchase(vente, attr, touches, maintenant):
    """vente + sa ligne attribution + touches de la personne -> evenement Purchase
    pour /events. PURE. None si la vente n'a pas d'email (rien a apparier)."""
    email = (vente.get("email") or "").strip().lower()
    if not email:
        return None
    jusqua = _iso(vente["purchased_at"])
    ts = min(int(jusqua.timestamp()), int(maintenant))
    raw = vente.get("raw") or {}
    attr = attr or {}
    user = {"em": [sha(email)], "external_id": [sha(email)]}
    if attr.get("vid"):
        user["external_id"].append(attr["vid"])
    prenom = str(raw.get("first_name") or "").strip()
    if prenom:
        user["fn"] = [sha(prenom)]
    fbc = _fbc_de(touches, attr.get("premier_contact"), ts, jusqua)
    if fbc:
        user["fbc"] = fbc
    for cle, champ in (("fbp", "fbp"), ("ip", "client_ip_address"), ("ua", "client_user_agent")):
        t = _derniere_touche_avec(touches, cle, jusqua)
        if t:
            user[champ] = t[cle]
    page = str(raw.get("page") or "").strip()
    url = page if page.startswith("http") else TUNNELS_URL.get(attr.get("tunnel") or "", URL_DEFAUT)
    return {
        "event_name": "Purchase",
        "event_time": ts,
        "event_id": eid_purchase(email, vente["id"]),
        "action_source": "website",
        "event_source_url": url,
        "user_data": user,
        "custom_data": {"value": float(vente.get("montant") or 0), "currency": "EUR",
                        "content_name": vente.get("produit") or ""},
    }


# ---------------------------------------------------------------- I/O

def _http_json(url, data=None, headers=None, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {"User-Agent": UA},
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt else None
    except urllib.error.HTTPError as e:
        raise RuntimeError("HTTP %s %s : %s" % (
            e.code, url.split("?")[0], e.read().decode()[:300])) from e


def sb(method, path, body=None, prefer=None):
    """Supabase REST avec la CLE SERVICE (le robot ecrit ; RLS contournee)."""
    headers = {"apikey": SERVICE_KEY, "Authorization": "Bearer " + SERVICE_KEY,
               "Content-Type": "application/json", "User-Agent": UA}
    if prefer:
        headers["Prefer"] = prefer
    return _http_json(SUPABASE_URL + path,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers=headers, method=method)


def sb_upsert(table, rows, conflict):
    for i in range(0, len(rows), 500):
        sb("POST", "/rest/v1/%s?on_conflict=%s" % (table, conflict), rows[i:i + 500],
           prefer="resolution=merge-duplicates,return=minimal")


def sio(path, **params):
    url = "https://api.systeme.io/api/" + path + \
          ("?" + urllib.parse.urlencode(params) if params else "")
    return _http_json(url, headers={"X-API-Key": SYSTEME_API_KEY, "User-Agent": UA})


def meta_depenses(since, until, fetch=None):
    """Insights level=ad, une ligne par pub PAR JOUR, avec pagination."""
    fetch = fetch or _http_json
    url = "https://graph.facebook.com/v25.0/act_%s/insights?%s" % (META_ACCOUNT, urllib.parse.urlencode({
        "level": "ad", "time_increment": 1, "limit": 200,
        "fields": "ad_id,ad_name,adset_id,adset_name,campaign_id,campaign_name,"
                  "spend,impressions,clicks",
        "time_range": json.dumps({"since": since, "until": until}),
        "access_token": META_TOKEN,
    }))
    rows = []
    while url:
        d = fetch(url)
        rows += [ligne_depense(r) for r in d.get("data", [])]
        url = (d.get("paging") or {}).get("next")
    return rows


def sb_all(path):
    """GET pagine : Supabase plafonne chaque reponse a 1000 lignes."""
    out, pas = [], 1000
    sep = "&" if "?" in path else "?"
    for offset in range(0, 10_000_000, pas):
        lot = sb("GET", "%s%soffset=%d&limit=%d" % (path, sep, offset, pas)) or []
        out += lot
        if len(lot) < pas:
            return out


def run_depenses(dry=False, backfill=None):
    """J-3 -> J a chaque passage (Meta corrige ses chiffres 3 jours).
    backfill='AAAA-MM-JJ' (one-shot, a la main) : repart de cette date pour
    donner au ROAS historique sa depense."""
    today = datetime.date.today()
    since = backfill or (today - datetime.timedelta(days=3)).isoformat()
    rows = meta_depenses(since, today.isoformat())
    print("Meta : %d lignes pub/jour (%s -> %s)" % (len(rows), since, today))
    if rows and not dry:
        sb_upsert("depenses_ads", rows, "date,ad_id")


def contacts_nouveaux(depuis, sio_fn=None, plafond=60000):
    """Passe (a) : tous les contacts APRES le curseur. Rend (lignes, nouveau_curseur)."""
    sio_fn = sio_fn or sio
    rows, after = [], depuis
    while True:
        p = {"limit": 100}
        if after:
            p["startingAfter"] = after
        d = sio_fn("contacts", **p)
        items = d.get("items", [])
        rows += [ligne_contact(c) for c in items]
        if items:
            after = str(items[-1]["id"])
        if not d.get("hasMore") or not items or len(rows) >= plafond:
            break
    return rows, after


def contacts_du_tag(tag, sio_fn=None):
    """Passe (b) : contacts d'un tag (base d'inscription reelle d'un live).
    Meme logique que welya_auto_sync.contacts_du_tag."""
    sio_fn = sio_fn or sio
    rows, after = [], None
    while True:
        p = {"tags": tag, "limit": 100}
        if after:
            p["startingAfter"] = after
        d = sio_fn("contacts", **p)
        items = d.get("items", [])
        rows += [ligne_contact(c) for c in items]
        if not d.get("hasMore") or not items or len(rows) > 20000:
            break
        after = str(items[-1]["id"])
    return rows


def contact_par_email(email, sio_fn=None):
    """Passe (c) : relecture fraiche du contact d'une vente (les champs utm
    sont ECRASES par une reinscription, la copie locale peut etre perimee)."""
    sio_fn = sio_fn or sio
    d = sio_fn("contacts", email=email, limit=10)  # l'API SIO refuse limit < 10 (422)
    items = d.get("items", [])
    return items[0] if items else None


def _sauve_curseur(dernier):
    sb_upsert("sync_state",
              [{"key": "contacts_sio_curseur", "value": {"dernier_id": dernier},
                "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat()}],
              "key")


def run_contacts(dry=False):
    etat = (sb("GET", "/rest/v1/sync_state?key=eq.contacts_sio_curseur&select=value") or [])
    depuis = (etat[0]["value"] or {}).get("dernier_id") if etat else None
    # (a) nouveaux, PAR TRANCHES de 5000 : le curseur et les lignes sont persistes
    # a chaque tranche, un crash a mi-parcours ne perd pas le travail fait
    # (premier passage = tout l'historique, potentiellement des centaines de pages)
    total = 0
    while True:
        rows, dernier = contacts_nouveaux(depuis, plafond=5000)
        total += len(rows)
        if rows and not dry:
            sb_upsert("contacts_sio", rows, "contact_id")
            _sauve_curseur(dernier)
        if len(rows) < 5000:
            break
        depuis = dernier
    print("SIO nouveaux : %d contacts (curseur -> %s)" % (total, dernier))
    rows = []

    # (b) re-scan des tags des lives a +/-30 j : champs/tags rafraichis
    lives = {k: v for k, v in json.loads(LIVES_PATH.read_text(encoding="utf-8")).items()
             if not k.startswith("_")}
    today = datetime.date.today()
    for d_live, cfg in sorted(lives.items()):
        if abs((datetime.date.fromisoformat(d_live) - today).days) <= 30:
            retag = contacts_du_tag(cfg["tag"])
            print("SIO tag %s (%s) : %d contacts rafraichis" % (cfg["tag"], d_live, len(retag)))
            rows += retag

    if rows and not dry:
        # dedoublonne par contact_id (un contact peut porter plusieurs tags re-scannes)
        vus = {}
        for r in rows:
            vus[r["contact_id"]] = r
        sb_upsert("contacts_sio", list(vus.values()), "contact_id")


def touches_de(email):
    """Toutes les touches de la personne : email -> vids (identites) -> touches,
    plus les touches portant directement l'email."""
    vids = [r["vid"] for r in
            (sb("GET", "/rest/v1/identites?email=eq.%s&select=vid" % urllib.parse.quote(email)) or [])]
    touches = list(sb_all("/rest/v1/touches?email=eq.%s&select=*&order=ts,id" % urllib.parse.quote(email)) or [])
    if vids:
        q = ",".join('"%s"' % v for v in vids)
        touches += sb_all("/rest/v1/touches?vid=in.(%s)&select=*&order=ts,id" % urllib.parse.quote(q)) or []
    vues = {}
    for t in touches:
        vues[t["id"]] = t
    return list(vues.values())


def faut_relire_contact(touches):
    """Passe (c) requise quand la personne n'a AUCUNE touche autre que l'achat
    lui-meme (donc aucun utm connu localement)."""
    return not [t for t in touches if t.get("type") != "achat"]


def run_attribution(recalc=False, dry=False):
    ventes = sb_all("/rest/v1/ventes?select=id,email,montant,produit,purchased_at"
                    "&order=purchased_at")
    deja = set() if recalc else {r["vente_id"] for r in
                                 sb_all("/rest/v1/attribution?select=vente_id&order=vente_id")}
    a_faire = [v for v in ventes if v["id"] not in deja]
    print("Attribution : %d vente(s) a traiter / %d en base" % (len(a_faire), len(ventes)))

    for v in a_faire:
        try:
            email = (v.get("email") or "").strip().lower()
            touches = touches_de(email) if email else []
            contact = None
            if email and faut_relire_contact(touches):
                contact = contact_par_email(email)          # passe (c)
                if contact and not dry:
                    sb_upsert("contacts_sio", [ligne_contact(contact)], "contact_id")
            row = attribuer(v, touches, contact)
            print("  %s %s : %s / %s / %s" % (
                str(v["purchased_at"])[:10], email or "(sans email)",
                row["modele"], row["canal"] or "-", row["slug_crea"] or "-"))
            if dry:
                continue
            sb_upsert("attribution", [row], "vente_id")
            sb_upsert("touches", [{
                "id": achat_touche_id(v["id"]), "vid": row["vid"],
                "ts": v["purchased_at"], "type": "achat", "email": email or None,
                "extra": {"vente_id": v["id"], "montant": v.get("montant"),
                          "produit": v.get("produit")},
            }], "id")
            if row["vid"] and email:
                sb_upsert("identites", [{"vid": row["vid"], "email": email,
                                         "source": "vente"}], "vid,email")
        except Exception as e:
            print("  ERREUR vente %s : %s" % (v.get("id"), e))
            continue


if __name__ == "__main__":
    dry = "--dry-run" in sys.argv
    if not SERVICE_KEY:
        sys.exit("SUPABASE_SERVICE_KEY manquant (le robot ecrit, la publishable ne suffit pas)")
    if not SYSTEME_API_KEY:
        sys.exit("SYSTEME_API_KEY manquant")
    backfill = None
    if "--backfill" in sys.argv:
        backfill = sys.argv[sys.argv.index("--backfill") + 1]
    echecs = []
    if META_TOKEN and "--skip-meta" not in sys.argv:
        try:
            run_depenses(dry=dry, backfill=backfill)
        except Exception as e:
            print("Meta skip (%s)" % str(e)[:200])
            echecs.append("depenses")
    else:
        print("Meta : saute (META_TOKEN absent ou --skip-meta)")
    try:
        run_contacts(dry=dry)
    except Exception as e:
        print("SIO contacts skip (%s)" % str(e)[:200])
        echecs.append("contacts")
    run_attribution(recalc="--recalc" in sys.argv, dry=dry)
    print("DONE" + (" (dry-run, rien ecrit)" if dry else ""))
    if echecs:
        # le job GitHub Actions doit passer ROUGE : une etape sautee en continu
        # serait une degradation silencieuse (mail d'echec = seule alerte)
        sys.exit("etapes en echec : " + ", ".join(echecs))
