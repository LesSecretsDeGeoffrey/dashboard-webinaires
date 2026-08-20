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
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

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
    if m == "lien":
        return "lien"
    return "organique"


def est_payante(t):
    return canal_de(t.get("utm_source"), t.get("utm_medium")) == "ads"


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
    return datetime.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


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
        payantes = [t for t in utiles if est_payante(t)]
        retenue = payantes[-1] if payantes else premier
        row.update({
            "modele": "last_paid" if payantes else "first",
            "vid": retenue.get("vid") or dernier.get("vid"),
            "premier_contact": _contact_jsonb(premier),
            "dernier_contact": _contact_jsonb(dernier),
            "dernier_contact_payant": _contact_jsonb(payantes[-1]) if payantes else None,
            "canal": canal_de(retenue.get("utm_source"), retenue.get("utm_medium")),
            "canal_dernier": canal_de(dernier.get("utm_source"), dernier.get("utm_medium")),
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
