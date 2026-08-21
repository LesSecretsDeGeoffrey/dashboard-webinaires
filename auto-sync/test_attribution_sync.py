#!/usr/bin/env python3
"""Tests des fonctions PURES d'attribution_sync.py. Aucun reseau, aucun secret."""
import datetime
import json
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
    # Lien court SEUL (aucun utm_source/medium) : compte deja comme source pour
    # dernier_contact, meme si canal_de ne sait pas le classer (organique).
    touches = [T("2026-08-02T10:00:00+00:00", slug="promo"),
               T("2026-08-09T10:00:00+00:00")]
    r = a.attribuer(VENTE, touches, contact=None)
    assert r["dernier_contact"]["slug"] == "promo"
    assert r["canal_dernier"] == "organique"
    # Cas reel (click_go) : lien court + utm manychat/lien -> classe correctement
    touches2 = [T("2026-08-02T10:00:00+00:00", slug="promo", src="manychat", med="lien"),
                T("2026-08-09T10:00:00+00:00")]
    r2 = a.attribuer(VENTE, touches2, contact=None)
    assert r2["canal_dernier"] == "manychat"

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
    """Plafond Supabase (1000 lignes/reponse) : touches_de doit pagine via sb_all,
    avec un ordre total (ts,id) pour un offset deterministe."""
    seen = []
    def fake_sb(m, p, body=None, prefer=None):
        seen.append(p)
        if "identites" in p:
            return []
        debut, n = (0, 1000) if "offset=0&" in p else (1000, 1) if "offset=1000" in p else (0, 0)
        return [{"id": str(debut + i), "ts": "t"} for i in range(n)]
    a.sb, orig = fake_sb, a.sb
    try:
        assert len(a.touches_de("x@y.fr")) == 1001
        assert all("order=ts,id" in p for p in seen if "touches" in p)
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


# --- Phase 3 : Purchase CAPI ---

from pathlib import Path

def test_empreinte_vendorisee_identique_a_la_source():
    """auto-sync/empreinte.py est une COPIE de ads-atelier-macarons-2aout/empreinte.py
    (le robot tourne dans ce depot seul en CI). Toute modif se fait a la source
    puis se recopie : ce test le garantit en local, saute en CI (source absente)."""
    import pytest
    src = Path(a.HERE).parent.parent / "ads-atelier-macarons-2aout" / "empreinte.py"
    if not src.exists():
        pytest.skip("source absente (CI)")
    assert (Path(a.HERE) / "empreinte.py").read_bytes() == src.read_bytes()

def test_eid_purchase_deterministe_et_normalise():
    e1 = a.eid_purchase("Jean.Dupont@Gmail.com ", "dec25bf4-0090-4bda-bb97-1c5d7e58663d")
    e2 = a.eid_purchase("jean.dupont@gmail.com", "dec25bf4-0090-4bda-bb97-1c5d7e58663d")
    assert e1 == e2
    assert e1.startswith("purchase-") and len(e1) == len("purchase-") + 16
    # une autre vente du meme email = un autre identifiant (pas de fusion cote Meta)
    assert a.eid_purchase("jean.dupont@gmail.com", "autre-id") != e1


# VENTE_P3, pas VENTE : un fixture VENTE existe deja (phase 1, ligne ~108) et deux tests
# d'attribution le lisent a l'appel -> le redefinir ici les casserait.
VENTE_P3 = {"id": "dec25bf4-0090-4bda-bb97-1c5d7e58663d", "email": "Jean@Gmail.com",
         "montant": 498, "produit": "MFP 3x166€", "purchased_at": "2026-08-20T18:30:00+00:00",
         "raw": {"first_name": "Jean", "page": "https://www.lessecretsdegeoffrey.fr/paiementfondationspro"}}
T0 = datetime.datetime(2026, 8, 21, 12, 0, tzinfo=datetime.timezone.utc).timestamp()

def _t(ts, **k):
    d = {"id": str(uuid.uuid4()), "vid": "v1", "ts": ts, "type": "pageview"}
    d.update(k)
    return d

def test_evenement_purchase_complet():
    touches = [
        _t("2026-08-20T18:25:00+00:00", type="identite", contexte="checkout",
           fbc="fb.1.200.NEW", fbp="fb.1.1.222", ip="2.2.2.2", ua="UA-new"),
        _t("2026-08-18T10:00:00+00:00", fbc="fb.1.100.OLD", fbp="fb.1.1.111", ip="1.1.1.1", ua="UA-old"),
        _t("2026-08-20T18:30:00+00:00", type="achat", vid="v1"),   # fabriquee par le robot : ignoree
    ]
    attr = {"vid": "v1", "modele": "last_paid", "tunnel": "live", "premier_contact": {}}
    e = a.evenement_purchase(VENTE_P3, attr, touches, T0)
    assert e["event_name"] == "Purchase" and e["action_source"] == "website"
    assert e["event_time"] == int(a._iso(VENTE_P3["purchased_at"]).timestamp())
    assert e["event_id"] == a.eid_purchase("jean@gmail.com", VENTE_P3["id"])
    assert e["event_source_url"] == "https://www.lessecretsdegeoffrey.fr/paiementfondationspro"
    u = e["user_data"]
    assert u["em"] == [a.sha("jean@gmail.com")] and u["fn"] == [a.sha("Jean")]
    assert u["external_id"] == [a.sha("jean@gmail.com"), "v1"]
    assert u["fbc"] == "fb.1.200.NEW" and u["fbp"] == "fb.1.1.222"
    assert u["client_ip_address"] == "2.2.2.2" and u["client_user_agent"] == "UA-new"
    assert e["custom_data"] == {"value": 498.0, "currency": "EUR", "content_name": "MFP 3x166€"}

def test_evenement_purchase_sio_contact_sans_touches():
    """Acheteur d'avant le snippet : pas d'IP/UA, fbc reconstruit depuis le fbclid du contact."""
    attr = {"vid": None, "modele": "sio_contact", "tunnel": "live",
            "premier_contact": {"utm_source": "fb", "fbclid": "ABC"}}
    v = dict(VENTE_P3, raw={"source": "saisie manuelle"})
    e = a.evenement_purchase(v, attr, [], T0)
    u = e["user_data"]
    assert u["external_id"] == [a.sha("jean@gmail.com")]
    assert "fn" not in u and "client_ip_address" not in u and "client_user_agent" not in u
    assert u["fbc"] == "fb.1.%d.ABC" % (e["event_time"] * 1000)
    assert e["event_source_url"] == a.TUNNELS_URL["live"]

def test_evenement_purchase_fbc_reconstruit_depuis_touche_fbclid():
    touches = [_t("2026-08-19T10:00:00+00:00", fbclid="XYZ")]
    e = a.evenement_purchase(VENTE_P3, {"vid": "v1", "tunnel": "ebook", "premier_contact": {}}, touches, T0)
    assert e["user_data"]["fbc"] == "fb.1.%d.XYZ" % int(a._iso("2026-08-19T10:00:00+00:00").timestamp() * 1000)

def test_evenement_purchase_url_par_tunnel_si_raw_page_pas_une_url():
    v = dict(VENTE_P3, raw={"page": "Page de confirmation"})
    assert a.evenement_purchase(v, {"tunnel": "call", "premier_contact": {}}, [], T0)["event_source_url"] == a.TUNNELS_URL["call"]
    assert a.evenement_purchase(v, {"tunnel": "autre", "premier_contact": {}}, [], T0)["event_source_url"] == a.URL_DEFAUT
    assert a.evenement_purchase(dict(VENTE_P3, raw=None), {"tunnel": None, "premier_contact": None}, [], T0)["event_source_url"] == a.URL_DEFAUT

def test_evenement_purchase_event_time_jamais_futur():
    v = dict(VENTE_P3, purchased_at="2026-08-21T12:00:05+00:00")   # 5 s dans le futur (horloge Make)
    assert a.evenement_purchase(v, {"premier_contact": {}}, [], T0)["event_time"] == int(T0)

def test_evenement_purchase_sans_email():
    assert a.evenement_purchase(dict(VENTE_P3, email="  "), {"premier_contact": {}}, [], T0) is None

def test_evenement_purchase_ignore_les_touches_posterieures_a_l_achat():
    """Une pageview du lendemain (avant le passage du cron) n'est pas une cause de l'achat."""
    touches = [_t("2026-08-20T18:00:00+00:00", fbc="fb.1.100.AVANT", ip="1.1.1.1", ua="UA-avant"),
               _t("2026-08-21T09:00:00+00:00", fbc="fb.1.200.APRES", ip="9.9.9.9", ua="UA-apres")]
    u = a.evenement_purchase(VENTE_P3, {"vid": "v1", "premier_contact": {}}, touches, T0)["user_data"]
    assert u["fbc"] == "fb.1.100.AVANT" and u["client_ip_address"] == "1.1.1.1" and u["client_user_agent"] == "UA-avant"

def test_evenement_purchase_egalite_de_ts_prend_la_derniere_de_la_liste():
    """Meme seconde (identite checkout + pageview) : la DERNIERE de la liste gagne, comme attribuer()."""
    touches = [_t("2026-08-20T18:25:00+00:00", ip="1.1.1.1"), _t("2026-08-20T18:25:00+00:00", ip="2.2.2.2")]
    assert a.evenement_purchase(VENTE_P3, {"premier_contact": {}}, touches, T0)["user_data"]["client_ip_address"] == "2.2.2.2"

def test_evenement_purchase_raw_page_et_prenom_nettoyes():
    v = dict(VENTE_P3, raw={"first_name": "   ", "page": " https://www.lessecretsdegeoffrey.fr/paiementbook "})
    e = a.evenement_purchase(v, {"tunnel": "live", "premier_contact": {}}, [], T0)
    assert "fn" not in e["user_data"]
    assert e["event_source_url"] == "https://www.lessecretsdegeoffrey.fr/paiementbook"
    # raw.page qui n'est pas une chaine : pas de plantage, repli sur le tunnel
    assert a.evenement_purchase(dict(VENTE_P3, raw={"page": {"u": 1}}), {"tunnel": "live", "premier_contact": {}}, [], T0)["event_source_url"] == a.TUNNELS_URL["live"]


def _v(i, email="a@b.fr", produit="MFP 497€", jours=1):
    ts = datetime.datetime.fromtimestamp(T0, datetime.timezone.utc) - datetime.timedelta(days=jours)
    return {"id": "v%d" % i, "email": email, "produit": produit, "montant": 497,
            "purchased_at": ts.isoformat()}

def test_ventes_a_envoyer_fenetre_7j():
    ventes = [_v(1, jours=8), _v(2, email="b@b.fr", jours=6.9), _v(3, email="c@b.fr", jours=0)]
    env, dbl = a.ventes_a_envoyer(ventes, [], T0)
    assert [v["id"] for v in env] == ["v2", "v3"] and dbl == []

def test_ventes_a_envoyer_ignore_deja_envoyees_mais_pas_les_tests():
    ventes = [_v(1), _v(2, email="b@b.fr"), _v(3, email="c@b.fr")]
    envois = [{"vente_id": "v1", "statut": "ok", "test": False},
              {"vente_id": "v2", "statut": "ok", "test": True},      # envoi test : ne compte pas
              {"vente_id": "v3", "statut": "erreur", "test": False}]  # erreur reelle : pas rejouee sans retry
    env, _ = a.ventes_a_envoyer(ventes, envois, T0)
    assert [v["id"] for v in env] == ["v2"]
    env, _ = a.ventes_a_envoyer(ventes, envois, T0, retry=True)
    assert [v["id"] for v in env] == ["v2", "v3"]

def test_ventes_a_envoyer_doublon_meme_email_meme_produit():
    # v1 envoyee ok il y a 30 j ; v2 = echeance rejouee par le webhook (meme email, meme plan)
    # v3 = meme email, AUTRE produit : legitime ; v4/v5 = deux lignes du meme lot
    ventes = [_v(1, jours=30), _v(2, jours=1), _v(3, produit="Ebook 90 jours", jours=1),
              _v(4, email="d@b.fr", jours=2), _v(5, email="d@b.fr", jours=1)]
    envois = [{"vente_id": "v1", "statut": "ok", "test": False}]
    env, dbl = a.ventes_a_envoyer(ventes, envois, T0)
    assert [v["id"] for v in env] == ["v4", "v3"]
    assert sorted(v["id"] for v in dbl) == ["v2", "v5"]

def test_ventes_a_envoyer_retry_rend_doublon_si_la_cle_est_deja_partie():
    """v1 en erreur reelle, v2 (meme email, meme produit) envoyee ok ensuite :
    avec --capi-retry, v1 est readmise puis classee doublon, jamais envoyee."""
    ventes = [_v(1, jours=2), _v(2, jours=1)]
    envois = [{"vente_id": "v1", "statut": "erreur", "test": False},
              {"vente_id": "v2", "statut": "ok", "test": False}]
    env, dbl = a.ventes_a_envoyer(ventes, envois, T0, retry=True)
    assert env == [] and [v["id"] for v in dbl] == ["v1"]

def test_ventes_a_envoyer_sans_email_ecartee():
    env, dbl = a.ventes_a_envoyer([_v(1, email="")], [], T0)
    assert env == [] and dbl == []


# --- envoyer_purchase / options_capi / run_capi (Task 4) ---

def test_envoyer_purchase_ok_erreur_et_code_test():
    vus = []
    def post_ok(url, data=None, headers=None, method=None):
        vus.append((url, json.loads(data.decode()), method))
        return {"events_received": 1, "fbtrace_id": "X"}
    evt = {"event_name": "Purchase", "event_id": "purchase-1"}
    statut, rep = a.envoyer_purchase(evt, test_code="TEST123", post=post_ok)
    assert statut == "ok" and rep["events_received"] == 1
    url, corps, method = vus[0]
    assert url.endswith("/%s/events" % a.PIXEL) and method == "POST"
    assert corps["data"] == [evt] and corps["test_event_code"] == "TEST123" and "access_token" in corps
    # sans code test : pas de cle test_event_code
    a.envoyer_purchase(evt, post=post_ok)
    assert "test_event_code" not in vus[1][1]
    # refus Meta (HTTPError relevee par _http_json en RuntimeError) : statut erreur + texte garde
    def post_ko(url, **k):
        raise RuntimeError("HTTP 400 https://graph.facebook.com/... : {\"error\":{\"message\":\"Invalid parameter\"}}")
    statut, rep = a.envoyer_purchase(evt, post=post_ko)
    assert statut == "erreur" and "Invalid parameter" in rep["erreur"]
    # events_received != 1 = erreur aussi (jamais 'ok' par defaut)
    assert a.envoyer_purchase(evt, post=lambda *a_, **k: {"events_received": 0})[0] == "erreur"

def test_options_capi_cli():
    o = a.options_capi(["--capi-test", "TEST1", "--capi-forcer", "--seulement-capi"])
    assert o == {"mode": "test", "test_code": "TEST1", "retry": False, "forcer": True, "seulement": True}
    assert a.options_capi(["--capi", "--capi-retry"])["mode"] == "go"
    assert a.options_capi(["--capi", "--capi-retry"])["retry"] is True
    assert a.options_capi(["--recalc"])["mode"] is None
    # --capi-forcer sans mode test est refuse (ne jamais forcer un envoi reel) ; --capi-test sans code aussi
    import pytest
    with pytest.raises(SystemExit):
        a.options_capi(["--capi", "--capi-forcer"])
    with pytest.raises(SystemExit):
        a.options_capi(["--capi-test"])
    with pytest.raises(SystemExit):
        a.options_capi(["--capi-test", "--seulement-capi"])
    with pytest.raises(SystemExit):
        a.options_capi(["--capi-test", "   "])      # code vide = envoi reel deguise : refuse
    with pytest.raises(SystemExit):
        a.options_capi(["--capi-tset", "X"])
    with pytest.raises(SystemExit):
        a.options_capi(["--seulement-capi"])

def test_run_capi_forcer_ne_touche_jamais_une_vente_deja_envoyee(monkeypatch):
    """--capi-forcer choisit la derniere vente SANS ligne reelle au journal : sinon
    l'upsert ecraserait la ligne test=false et le cron renverrait la vente pour de vrai."""
    ventes = [_v(1, jours=30), _v(2, email="b@b.fr", jours=20)]     # v2 = la plus recente, deja envoyee
    attrs = [{"vente_id": "v1", "vid": None, "modele": "sio_contact", "premier_contact": {}, "tunnel": "live"},
             {"vente_id": "v2", "vid": None, "modele": "sio_contact", "premier_contact": {}, "tunnel": "live"}]
    envois = [{"vente_id": "v2", "statut": "ok", "test": False}]
    def sb_all_fake(path):
        if "/ventes?" in path: return ventes
        if "/attribution?" in path: return attrs
        if "/capi_envois?" in path: return envois
        raise AssertionError(path)
    ecrits = []
    monkeypatch.setattr(a, "sb_all", sb_all_fake)
    monkeypatch.setattr(a, "touches_de", lambda email: [])
    monkeypatch.setattr(a, "envoyer_purchase", lambda evt, code=None: ("ok", {"events_received": 1}))
    monkeypatch.setattr(a, "sb_upsert", lambda table, rows, conflict: ecrits.append((table, rows)))
    o = {"mode": "test", "test_code": "T", "retry": False, "forcer": True, "seulement": True}
    assert a.run_capi(o) == 0
    assert [r["vente_id"] for t, rows in ecrits for r in rows] == ["v1"]     # jamais v2
    assert ecrits[0][1][0]["test"] is True


# --- run_capi : contrat reel (refus compte, doublon journalise, dry-run inerte) ---

def _v_now(i, email="a@b.fr", produit="MFP 497€", heures=1):
    ts = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=heures)
    return {"id": "v%d" % i, "email": email, "produit": produit, "montant": 497,
            "purchased_at": ts.isoformat()}

def _attr(vid):
    return {"vente_id": vid, "vid": None, "modele": "sio_contact", "premier_contact": {}, "tunnel": "live"}

def _branche(monkeypatch, ventes, envois, reponses):
    """reponses : evt -> (statut, rep). Rend la liste des lignes upsertees."""
    def sb_all_fake(path):
        if "/ventes?" in path: return ventes
        if "/attribution?" in path: return [_attr(v["id"]) for v in ventes]
        if "/capi_envois?" in path: return envois
        raise AssertionError(path)
    ecrits = []
    monkeypatch.setattr(a, "sb_all", sb_all_fake)
    monkeypatch.setattr(a, "touches_de", lambda email: [])
    monkeypatch.setattr(a, "envoyer_purchase", lambda evt, code=None: reponses(evt))
    monkeypatch.setattr(a, "sb_upsert", lambda table, rows, conflict: ecrits.extend(rows))
    return ecrits

GO = {"mode": "go", "test_code": None, "retry": False, "forcer": False, "seulement": True}
TEST = {"mode": "test", "test_code": "T", "retry": False, "forcer": False, "seulement": True}

def test_run_capi_refus_meta_compte_et_les_autres_partent(monkeypatch):
    ventes = [_v_now(1, heures=3), _v_now(2, email="b@b.fr", heures=2)]
    def rep(evt):
        return ("erreur", {"erreur": "HTTP 400 Invalid parameter"}) \
            if evt["event_id"] == a.eid_purchase("a@b.fr", "v1") else ("ok", {"events_received": 1})
    ecrits = _branche(monkeypatch, ventes, [], rep)
    assert a.run_capi(dict(GO)) == 1
    par = {r["vente_id"]: r for r in ecrits}
    assert par["v1"]["statut"] == "erreur" and par["v2"]["statut"] == "ok"
    assert all(r["test"] is False for r in ecrits)

def test_run_capi_doublon_journalise_en_reel_pas_en_test(monkeypatch):
    ventes = [_v_now(1, heures=3), _v_now(2, heures=2)]      # meme email + meme produit = echeance rejouee
    ok = lambda evt: ("ok", {"events_received": 1})
    ecrits = _branche(monkeypatch, ventes, [], ok)
    assert a.run_capi(dict(GO)) == 0
    par = {r["vente_id"]: r for r in ecrits}
    assert par["v1"]["statut"] == "ok" and par["v2"]["statut"] == "doublon" and par["v2"]["test"] is False
    ecrits = _branche(monkeypatch, ventes, [], ok)
    a.run_capi(dict(TEST))
    assert [r["vente_id"] for r in ecrits] == ["v1"]     # pas de ligne doublon test=false en mode test

def test_run_capi_dry_run_n_envoie_ni_n_ecrit(monkeypatch):
    appels = []
    ecrits = _branche(monkeypatch, [_v_now(1)], [], lambda evt: appels.append(evt) or ("ok", {}))
    assert a.run_capi(dict(GO), dry=True) == 0
    assert appels == [] and ecrits == []

def test_run_capi_refus_en_mode_test_compte_aussi(monkeypatch):
    """Token/payload refuse pendant un --capi-test : le job doit passer ROUGE, pas vert
    avec une ligne ERREUR enterree dans le log."""
    ecrits = _branche(monkeypatch, [_v_now(1)], [], lambda evt: ("erreur", {"erreur": "x"}))
    assert a.run_capi(dict(TEST)) == 1
    assert ecrits[0]["statut"] == "erreur" and ecrits[0]["test"] is True

def test_run_capi_journal_ko_apres_envoi_trace_et_releve(monkeypatch, capsys):
    """Envoi parti, upsert capi_envois KO : trace identifiable (vente + event_id) puis
    l'exception remonte, la passe tombe et le job passe rouge."""
    import pytest
    _branche(monkeypatch, [_v_now(1)], [], lambda evt: ("ok", {"events_received": 1}))
    def upsert_ko(table, rows, conflict):
        raise RuntimeError("HTTP 503 supabase")
    monkeypatch.setattr(a, "sb_upsert", upsert_ko)
    with pytest.raises(RuntimeError):
        a.run_capi(dict(GO))
    out = capsys.readouterr().out
    assert "JOURNAL KO apres envoi ok : vente v1 event_id " + a.eid_purchase("a@b.fr", "v1") in out

def test_workflow_attribution_porte_les_options_capi():
    yml = (Path(a.HERE).parent / ".github" / "workflows" / "attribution.yml").read_text(encoding="utf-8")
    for mot in ("capi_test_code", "capi_go", "capi_retry", "--capi-test", "--capi-forcer",
                "--seulement-capi", "--capi-retry", "CAPI_TEST_CODE"):
        assert mot in yml, mot
    assert "concurrency" in yml and "cancel-in-progress: false" in yml
