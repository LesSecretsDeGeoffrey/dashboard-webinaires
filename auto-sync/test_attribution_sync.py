#!/usr/bin/env python3
"""Tests des fonctions PURES d'attribution_sync.py. Aucun reseau, aucun secret."""
import uuid
import attribution_sync as a


# --- canal_de : parite stricte avec la fonction SQL du schema ---

def test_canal_de_paye():
    assert a.canal_de("fb", None) == "ads"
    assert a.canal_de("IG", "") == "ads"
    assert a.canal_de("autre", "paid") == "ads"
    assert a.canal_de("ads", "lien") == "ads"      # lien court canal=ads

def test_canal_de_canaux_nommes():
    for c in ("manychat", "email", "story", "bio", "whatsapp"):
        assert a.canal_de(c, None) == c
        assert a.canal_de(c.upper(), "lien") == c  # le canal nomme gagne sur medium=lien

def test_canal_de_lien_et_organique():
    assert a.canal_de("autre", "lien") == "lien"
    assert a.canal_de(None, None) == "organique"
    assert a.canal_de("", "") == "organique"
    assert a.canal_de("google", "organic") == "organique"

def test_canal_de_parite_sql():
    """La liste des cas ci-dessus DOIT matcher la fonction SQL canal_de du
    schema : toute modif se fait aux DEUX endroits (spec §7)."""
    sql = open(a.SCHEMA_PATH, encoding="utf-8").read().lower()
    for mot in ("'fb'", "'ig'", "'facebook'", "'instagram'", "'an'", "'msg'",
                "'meta'", "'ads'", "'manychat'", "'whatsapp'", "'lien'", "'organique'"):
        assert mot in sql, f"{mot} absent du schema SQL"
    assert "manychat" not in str(sorted(a.PAID)), "ManyChat n'est PAS payant ici"


# --- tunnel_de ---

def test_tunnel_par_path():
    assert a.tunnel_de("/paiementfondationspro", None) == "live"
    assert a.tunnel_de("https://www.lessecretsdegeoffrey.fr/paiementbook-direct", None) == "ebook"
    assert a.tunnel_de("/maitrise", None) == "ebook"
    assert a.tunnel_de("/paiementmethode997", None) == "call"

def test_tunnel_par_produit_quand_pas_de_path():
    assert a.tunnel_de(None, "La Methode Fondations Pro") == "live"
    assert a.tunnel_de("", "Maitriser la patisserie en 90 jours (ebook)") == "ebook"
    assert a.tunnel_de(None, "Formation Macarons 17") == "lowticket"
    assert a.tunnel_de(None, "objet inconnu") == "autre"


# --- ligne_depense (Meta insights level=ad -> ligne depenses_ads) ---

def test_ligne_depense():
    r = {"date_start": "2026-08-18", "ad_id": "1200", "ad_name": "recyc_img_macarons | Adset F35 | 0208",
         "adset_id": "a1", "adset_name": "Adset F35", "campaign_id": "c1",
         "campaign_name": "Atelier Macarons", "spend": "12.34", "impressions": "1000", "clicks": "57"}
    d = a.ligne_depense(r)
    assert d == {"date": "2026-08-18", "ad_id": "1200", "campaign_id": "c1",
                 "campaign_name": "Atelier Macarons", "adset_id": "a1", "adset_name": "Adset F35",
                 "ad_name": "recyc_img_macarons | Adset F35 | 0208",
                 "slug_crea": "recyc_img_macarons", "spend": 12.34,
                 "impressions": 1000, "clicks": 57}

def test_ligne_depense_sans_pipe():
    assert a.ligne_depense({"date_start": "d", "ad_id": "1", "ad_name": "SansPipe"})["slug_crea"] == "SansPipe"


# --- ligne_contact (contact SIO -> ligne contacts_sio) : reprend utm_of ---

CONTACT = {
    "id": 42, "email": "Jean@Gmail.com", "registeredAt": "2026-08-05T10:00:00+02:00",
    "fields": [{"slug": "utm_source", "value": "fb"}, {"slug": "utm_term", "value": "Adset F35"},
               {"slug": "utm_content", "value": "recyc_img_macarons"}, {"slug": "fbc", "value": "fb.1.1.X"}],
    "tags": [{"id": 2116742, "name": "atelier-0908"}],
}

def test_ligne_contact():
    c = a.ligne_contact(CONTACT)
    assert c["contact_id"] == "42"
    assert c["email"] == "jean@gmail.com"
    assert c["utm_source"] == "fb" and c["utm_content"] == "recyc_img_macarons"
    assert c["fbc"] == "fb.1.1.X"
    assert c["tags"] == ["2116742"]

def test_ligne_contact_source_url_en_repli():
    c = a.ligne_contact({"id": 1, "email": "a@b.fr", "fields": [],
                         "sourceURL": "https://x.fr/live2?utm_source=ig&utm_content=slug2"})
    assert c["utm_source"] == "ig" and c["utm_content"] == "slug2"


# --- attribuer : LE coeur. vente + touches + contact -> ligne attribution ---

def T(ts, src=None, med=None, term=None, content=None, typ="pageview", path="/", vid="v1"):
    return {"ts": ts, "utm_source": src, "utm_medium": med, "utm_term": term,
            "utm_content": content, "utm_id": None, "type": typ, "path": path, "vid": vid}

VENTE = {"id": "9f1b2c3d-0000-0000-0000-000000000001", "email": "Jean@Gmail.com",
         "montant": 497, "produit": "La Methode Fondations Pro",
         "purchased_at": "2026-08-09T21:30:00+00:00"}

def test_attribuer_last_paid():
    touches = [T("2026-07-20T10:00:00+00:00", src="manychat"),
               T("2026-08-01T10:00:00+00:00", src="fb", med="paid", term="Adset F35", content="recyc_img"),
               T("2026-08-09T10:00:00+00:00", src="email")]
    r = a.attribuer(VENTE, touches, contact=None)
    assert r["modele"] == "last_paid" and r["canal"] == "ads"
    assert r["slug_crea"] == "recyc_img" and r["adset_name"] == "Adset F35"
    assert r["canal_dernier"] == "email"          # l'ecran Canaux lit le dernier contact TOUS canaux
    assert r["premier_contact"]["utm_source"] == "manychat"
    assert r["nb_touches"] == 3
    assert r["email"] == "jean@gmail.com" and r["vente_id"] == VENTE["id"]

def test_attribuer_first_quand_rien_de_paye():
    touches = [T("2026-08-01T10:00:00+00:00", src="story"),
               T("2026-08-09T10:00:00+00:00", src="email")]
    r = a.attribuer(VENTE, touches, contact=None)
    assert r["modele"] == "first" and r["canal"] == "story" and r["canal_dernier"] == "email"

def test_attribuer_fenetre_90j_et_touches_achat_exclues():
    touches = [T("2026-01-01T10:00:00+00:00", src="fb", med="paid"),          # hors fenetre
               T("2026-08-09T22:00:00+00:00", src="fb", med="paid"),          # APRES l'achat
               T("2026-08-08T10:00:00+00:00", typ="achat", src="fb", med="paid"),  # exclue
               T("2026-08-05T10:00:00+00:00", src="bio")]
    r = a.attribuer(VENTE, touches, contact=None)
    assert r["modele"] == "first" and r["canal"] == "bio" and r["nb_touches"] == 1

def test_attribuer_repli_sio_contact():
    r = a.attribuer(VENTE, [], contact=CONTACT)
    assert r["modele"] == "sio_contact"
    assert r["canal"] == "ads" and r["canal_dernier"] == "ads"
    assert r["slug_crea"] == "recyc_img_macarons" and r["adset_name"] == "Adset F35"

def test_attribuer_contact_sans_utm_est_organique():
    r = a.attribuer(VENTE, [], contact={"id": 1, "email": "jean@gmail.com", "fields": []})
    assert r["modele"] == "sio_contact" and r["canal"] == "organique"

def test_attribuer_aucune():
    r = a.attribuer(VENTE, [], contact=None)
    assert r["modele"] == "aucune" and r["canal"] is None and r["canal_dernier"] is None
    assert r["slug_crea"] is None

def test_attribuer_tunnel_et_delai():
    touches = [T("2026-08-02T21:30:00+00:00", src="fb", med="paid", path="/live2")]
    r = a.attribuer(VENTE, touches, contact=None)
    assert r["tunnel"] == "live"     # produit Fondations (le path /live2 n'est pas une page de paiement)
    assert r["delai_j"] == 7.0

def test_touche_achat_id_deterministe():
    i1 = a.achat_touche_id(VENTE["id"])
    i2 = a.achat_touche_id(VENTE["id"])
    assert i1 == i2 and str(uuid.UUID(i1)) == i1


def test_meta_depenses_pagine():
    pages = [
        {"data": [{"date_start": "2026-08-18", "ad_id": "1", "ad_name": "s1 | A | 0208", "spend": "1"}],
         "paging": {"next": "https://graph.facebook.com/page2"}},
        {"data": [{"date_start": "2026-08-18", "ad_id": "2", "ad_name": "s2 | A | 0208", "spend": "2"}]},
    ]
    vus = []
    def fake_fetch(url):
        vus.append(url)
        return pages[len(vus) - 1]
    rows = a.meta_depenses("2026-08-15", "2026-08-18", fetch=fake_fetch)
    assert [r["ad_id"] for r in rows] == ["1", "2"]
    assert "level" in vus[0] and vus[1] == "https://graph.facebook.com/page2"
