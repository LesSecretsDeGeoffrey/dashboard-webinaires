#!/usr/bin/env python3
"""Tests des fonctions PURES d'attribution_sync.py. Aucun reseau, aucun secret."""
import datetime
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

def test_canal_de_manychat_dans_le_medium():
    """Terrain : utm_source=post + utm_medium=manychat-insta (acheteur cdanteny 09/08)."""
    assert a.canal_de("post", "manychat-insta") == "manychat"
    assert a.canal_de(None, "manychat") == "manychat"
    assert a.canal_de("fb", "manychat-insta") == "ads"   # payant gagne toujours


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
                "'meta'", "'ads'", "'manychat'", "'whatsapp'", "'lien'", "'organique'",
                "'%manychat%'"):
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

def T(ts, src=None, med=None, term=None, content=None, typ="pageview", path="/", vid="v1",
      slug=None):
    return {"ts": ts, "utm_source": src, "utm_medium": med, "utm_term": term,
            "utm_content": content, "utm_id": None, "type": typ, "path": path, "vid": vid,
            "slug": slug}

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

def test_attribuer_dernier_contact_ignore_les_touches_sans_source():
    """Terrain snippet : landing avec UTM -> pages internes SANS UTM -> identite
    au checkout SANS UTM. canal_dernier doit rester 'ads', pas degeneres en
    'organique' (regression vs phase 1 sur l'ecran Canaux)."""
    touches = [T("2026-08-02T10:00:00+00:00", src="fb", med="paid", term="Adset F35",
                 content="recyc_img", path="/live2"),
               T("2026-08-05T10:00:00+00:00", path="/paiementfondationspro"),
               T("2026-08-09T20:00:00+00:00", typ="identite", path="/paiementfondationspro")]
    r = a.attribuer(VENTE, touches, contact=None)
    assert r["modele"] == "last_paid" and r["canal"] == "ads"
    assert r["canal_dernier"] == "ads"
    assert r["dernier_contact"]["utm_source"] == "fb"
    assert r["tunnel"] == "live"     # derive de la VRAIE derniere touche (son path)
    assert r["nb_touches"] == 3

def test_attribuer_canal_dernier_story_puis_pages_internes():
    touches = [T("2026-08-02T10:00:00+00:00", src="story"),
               T("2026-08-05T10:00:00+00:00"),
               T("2026-08-09T10:00:00+00:00")]
    r = a.attribuer(VENTE, touches, contact=None)
    assert r["modele"] == "first" and r["canal"] == "story"
    assert r["canal_dernier"] == "story"

def test_attribuer_lien_court_compte_comme_source():
    touches = [T("2026-08-02T10:00:00+00:00", slug="promo", src="manychat", med="lien"),
               T("2026-08-09T10:00:00+00:00")]
    r = a.attribuer(VENTE, touches, contact=None)
    assert r["canal_dernier"] == "manychat"

def test_attribuer_visite_directe_reste_organique():
    touches = [T("2026-08-02T10:00:00+00:00"), T("2026-08-09T10:00:00+00:00")]
    r = a.attribuer(VENTE, touches, contact=None)
    assert r["canal"] == "organique" and r["canal_dernier"] == "organique"


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


def test_contacts_nouveaux_curseur():
    lots = {None: {"items": [{"id": 1, "email": "a@a.fr", "fields": [], "tags": []},
                             {"id": 2, "email": "b@b.fr", "fields": [], "tags": []}], "hasMore": True},
            "2":  {"items": [{"id": 3, "email": "c@c.fr", "fields": [], "tags": []}], "hasMore": False}}
    def fake_sio(path, **p):
        assert path == "contacts" and p.get("limit") == 100
        return lots[p.get("startingAfter")]
    rows, dernier = a.contacts_nouveaux(depuis=None, sio_fn=fake_sio)
    assert [r["contact_id"] for r in rows] == ["1", "2", "3"]
    assert dernier == "3"


def test_contacts_du_tag_pagine():
    appels = []
    lots = [
        {"items": [{"id": 10, "email": "a@a.fr", "fields": [], "tags": []},
                   {"id": 11, "email": "b@b.fr", "fields": [], "tags": []}], "hasMore": True},
        {"items": [{"id": 12, "email": "c@c.fr", "fields": [], "tags": []}], "hasMore": False},
    ]
    def fake_sio(path, **p):
        appels.append(p.get("startingAfter"))
        return lots[len(appels) - 1]
    rows = a.contacts_du_tag("atelier-0908", sio_fn=fake_sio)
    assert [r["contact_id"] for r in rows] == ["10", "11", "12"]
    assert appels[0] is None and appels[1] == "11"


def test_tunnel_997_prime_sur_fondations():
    assert a.tunnel_de(None, "Methode Fondations Pro 997") == "call"


def test_faut_relire_contact():
    assert a.faut_relire_contact([]) is True
    assert a.faut_relire_contact([T("2026-08-01T10:00:00+00:00", typ="achat")]) is True
    assert a.faut_relire_contact([T("2026-08-01T10:00:00+00:00", typ="achat"),
                                  T("2026-08-02T10:00:00+00:00")]) is False


def test_iso_fraction_courte():
    d = a._iso("2026-08-09T21:30:00.12345+00:00")
    assert d == datetime.datetime(2026, 8, 9, 21, 30, 0, 123450, tzinfo=datetime.timezone.utc)


def test_contact_par_email_limit_api():
    """L'API SIO exige 10 <= limit <= 100 : limit=1 repond 422 (vu au dry-run du 20/08)."""
    vus = {}
    def fake_sio(path, **p):
        vus.update(p)
        assert 10 <= p["limit"] <= 100
        return {"items": [{"id": 7, "email": "x@y.fr"}]}
    c = a.contact_par_email("x@y.fr", sio_fn=fake_sio)
    assert c["id"] == 7 and vus["email"] == "x@y.fr"


def test_touches_de_pagine():
    """Plafond Supabase (1000 lignes/reponse) : touches_de doit pagine via sb_all."""
    def fake_sb(m, p, body=None, prefer=None):
        if "identites" in p:
            return []
        debut, n = (0, 1000) if "offset=0&" in p else (1000, 1) if "offset=1000" in p else (0, 0)
        return [{"id": str(debut + i), "ts": "t"} for i in range(n)]
    a.sb, orig = fake_sb, a.sb
    try:
        assert len(a.touches_de("x@y.fr")) == 1001
    finally:
        a.sb = orig


def test_tunnel_fragments_presents_dans_le_sql():
    """Phase 2 : chaque fragment de TUNNELS_PATH doit exister dans la fonction
    SQL tunnel_de_path (ecran Tunnels). Le SQL peut en connaitre PLUS (pages
    d'optin comme /live2), jamais MOINS — meme discipline que canal_de."""
    sql = open(a.SCHEMA_PATH, encoding="utf-8").read().lower()
    assert "tunnel_de_path" in sql
    for frag, _ in a.TUNNELS_PATH:
        assert frag in sql, f"{frag} absent de tunnel_de_path"
    for vue in ("v_tunnel_etapes", "v_clics_liens"):
        assert vue in sql, f"vue {vue} absente du schema"
    for fn in ("upsert_visiteur", "clic_lien"):
        assert fn in sql, f"fonction {fn} absente du schema"
