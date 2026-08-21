# Dashboard Webinaires — Les Secrets De Geoffrey

Suivi de performance des webinaires (live atelier macarons + autres) : show-up, conversion, CA, objections, DM tracking.

## Stack

- Frontend statique HTML/CSS/JS vanilla
- Backend : Supabase (PostgreSQL hébergé)
- Hébergement : GitHub Pages
- Charts : Chart.js (CDN)
- CSV parsing : Papa Parse (CDN)
- Export PDF : html2pdf.js (CDN)

## Setup local

Ouvrir `index.html` dans Chrome. Password : `6688`.

## Setup base de données (1 seule fois)

Exécuter le fichier `supabase-schema.sql` dans Supabase Dashboard > SQL Editor.

## Déploiement

Push sur la branche `main` du repo GitHub. GitHub Pages déploie automatiquement.

## Ventes — remontée automatique

Depuis le 10/08/2026, chaque vente de La Méthode Fondations Pro arrive toute seule dans la
table `ventes` : Systeme.io → scénario Make `6834210` → Supabase. Montage et dépannage :
`webhook-ventes-systeme/README.md` (dépôt parent).

⚠️ `applyVentesAttribution()` **additionne** la table `ventes` et les colonnes
`ventes_count` / `ventes_montant_total` de la table `webinaires`. Ces colonnes servaient à la
saisie manuelle : **les laisser à 0**, sinon chaque vente est comptée deux fois.

## Attribution (phase 1) — quelle pub / quel canal ramène chaque vente

Page : `attribution.html` (lien « Attribution » dans la sidebar, même mot de passe).
Robot : `auto-sync/attribution_sync.py`, lancé 4×/jour par `.github/workflows/attribution.yml`
(⚠️ le workflow ne tourne qu'après un PUSH du dépôt). Schéma : `attribution-schema.sql`,
à jouer dans Supabase > SQL Editor — idempotent, à REJOUER après toute modification
(la fonction `canal_de` y vit ; sa copie Python est verrouillée par un test de parité).

Tables : `depenses_ads` (spend Meta par pub/jour, fenêtre J-3 rejouée), `contacts_sio`
(copie des contacts SIO en 3 passes : curseur nouveaux + re-scan des tags des lives ±30 j
+ relecture par email à chaque vente), `attribution` (une ligne par vente, recalculable via
`--recalc`), `touches`/`identites`/`visiteurs`/`liens` (préparées pour la phase 2 snippet).

Modèle d'attribution : dernier contact PAYANT avant l'achat, sinon premier contact, sinon
champs `utm_*` du contact SIO (`sio_contact`, le mode nominal en phase 1), sinon `aucune`
(ligne écrite quand même, jamais cachée). L'écran **Pubs** lit le modèle ; l'écran **Canaux**
lit `canal_dernier` (dernier contact tous canaux). ManyChat est reconnu dans le MEDIUM
(`utm_medium=manychat-insta`) et compte comme canal propre, pas comme ads.

Secrets GitHub du workflow : `SYSTEME_API_KEY`, `META_TOKEN`, `SUPABASE_SERVICE_KEY`
(clé service_role — le robot écrit, la publishable ne suffit pas). Une étape en échec fait
sortir le job en erreur (mail GitHub) sans empêcher les étapes suivantes du même run.

Run local :
`SYSTEME_API_KEY=… META_TOKEN=… SUPABASE_SERVICE_KEY=… python3 auto-sync/attribution_sync.py`
(`--dry-run` n'écrit rien · `--recalc` recalcule tout · `--backfill AAAA-MM-JJ` récupère la
dépense Meta historique · `--skip-meta` saute Meta). Tests : `python3 -m pytest auto-sync/test_attribution_sync.py`.

Limites connues (phase 1) : pas de parcours par personne tant que le snippet (phase 2) n'est
pas posé — l'attribution vient des champs du contact SIO, donc un habitué réinscrit porte les
UTM de sa DERNIÈRE inscription ; la dépense Meta ne remonte qu'à partir du premier run
(utiliser `--backfill` une fois pour l'historique).

## Attribution (phase 2) — parcours, tunnels, liens courts

`attribution.html` gagne une barre d'onglets : **Pubs · Canaux** (écran phase 1, inchangé),
**Parcours**, **Tunnels**, **Liens**.

- **Parcours** : email → `identites` (vids associés) → toutes les `touches` de ces vids,
  triées en timeline (pageview / identite / click_go / achat). Sert à vérifier à la main le
  chemin d'une personne. Un acheteur d'avant la pose du snippet n'a aucune touche — son
  attribution reste correcte via le modèle `sio_contact` de la phase 1, seul l'écran Parcours
  est vide pour lui.
- **Tunnels** : lit la vue `v_tunnel_etapes` (personnes distinctes par `path`, groupées par
  tunnel via `tunnel_de_path`). Le taux affiché est relatif à l'étape précédente DANS le
  même tunnel (ordre = plus de personnes d'abord).
- **Liens** : création de liens courts (`liens`, table RLS lecture + insert ouverts à la
  clé publishable) et suivi des clics. Le lien pointe vers `go.lessecretsdegeoffrey.fr/<slug>` ;
  seules les destinations dans la liste blanche du collecteur (`lessecretsdegeoffrey.fr`,
  `welya.io`, `instagram.com`, `wa.me`, `whatsapp.com`, `calendly.com`, `amzn.to`,
  `amazon.fr`) sont redirigées, sinon repli sur la home.
- **Canaux** gagne une colonne **Clics liens**, alimentée par la vue `v_clics_liens`
  (clics de liens courts par canal et par jour).

Policies `liens` : lecture + insert pour la clé publishable, aucune mise à jour
(durcissement RLS complet = hors périmètre, phase 3).

Ces trois écrans dépendent du collecteur `lsdg-track` (dépôt séparé, Cloudflare Pages
`go.lessecretsdegeoffrey.fr`) et du snippet posé sur le site — tant que l'un des deux n'est
pas en service, les écrans restent vides sans casser la page (`q()` rend `[]` sur un 404).

⚠️ Les vues et fonctions phase 2 (`v_tunnel_etapes`, `v_clics_liens`, `upsert_visiteur`,
`clic_lien`, `tunnel_de_path`) vivent dans le même `attribution-schema.sql` que la phase 1 —
**rejouer le fichier EN ENTIER dans Supabase > SQL Editor** après toute modification, comme
en phase 1 (schéma idempotent, pas de migration séparée).

## Attribution (phase 3) — Purchase CAPI

Le robot `auto-sync/attribution_sync.py` envoie chaque vente **attribuée** à Meta par l'API
Conversions (`Purchase`, pixel `2085581652276222`), **une requête par vente**, journal dans
`capi_envois` (`statut` ok · erreur · doublon, `reponse` brute de Meta, `test`).

Règles : `purchased_at` ≤ 7 j (Meta refuse au-delà → la première activation n'envoie pas
l'historique) · un `event_id` (`purchase-` + empreinte(email|vente_id), `auto-sync/empreinte.py`
= copie de `ads-atelier-macarons-2aout/empreinte.py`, parité testée) n'est envoyé qu'une fois pour
de vrai · même email + même produit déjà envoyé = `doublon` (échéance d'un Nx rejouée par le
webhook), pas renvoyé · `user_data` = em/fn hachés, `external_id` = sha(email) + vid,
`fbc`/`fbp`/IP/UA de la dernière touche du parcours antérieure à l'achat (mode `sio_contact` :
fbc du contact, sans IP/UA, Meta avertit sur la qualité, c'est attendu).

**Seul le serveur envoie Purchase** : avant d'activer le cron, on coupe les deux autres sources
relevées le 21/08 (la CAPI native de Systeme.io réglée sur le domaine, et le bloc navigateur v2 de
`/confirmation-paiement-mfp`), procédure dans `../tracking-attribution/README.md`. Le Lead n'est
pas touché.

Options : `--capi` (réel, sur le cron à partir de la Task 8) · `--capi-test TESTxxxxx` (outil
Évènements de test, `test=true`, + `--capi-forcer` = rejoue la dernière vente jamais envoyée pour
de vrai, datée de maintenant, s'il n'y en a aucune sur 7 j) · `--capi-retry` (rejoue les erreurs
réelles) · `--seulement-capi` (saute dépenses/contacts/attribution). Depuis GitHub → Actions →
« Attribution ventes » → Run workflow : champs `capi_test_code`, `capi_go`, `capi_retry`. Le
workflow a un groupe `concurrency` : un run manuel qui chevauche le cron attend son tour.

Tout refus de Meta (test OU réel) met le job en ROUGE (mail d'échec) : lire `capi_envois.reponse`
ou la ligne `ERREUR :` du log. Une ligne `JOURNAL KO apres envoi` = la vente est partie chez Meta
mais pas journalisée (Supabase KO) : le run suivant la renverra avec le même `event_id`, que Meta
dédoublonne pendant 48 h.
