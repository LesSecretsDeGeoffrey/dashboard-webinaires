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
pas posé ; la dépense Meta ne remonte qu'à partir du premier run (utiliser `--backfill` une
fois pour l'historique).

Réinscriptions (21/08/2026) : SIO écrase les champs `utm_*` d'un contact à chaque nouvelle
inscription. Le robot historise donc chaque jeu d'UTM distinct qu'il observe dans
`contacts_sio_utm` (insert-ignore, `vu_le` = première observation, jamais mis à jour), et le
repli `sio_contact` applique le même modèle que les touches : dernier jeu PAYANT observé avant
l'achat, sinon premier jeu connu. L'historique ne commence qu'au premier passage du robot
(20/08) : pour les contacts plus anciens, seul l'état courant est connu, comme avant.

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

## RDV Calendly (21/08/2026) — qui prend un call, d'où il vient, s'il achète

```
Calendly ──(webhook invitee.created / invitee.canceled)──▶ Make 7044512 ──▶ Supabase `rdv`
                                                                              │
snippet v1.1 : salesforce_uuid=<vid> sur tout lien/popup Calendly ────────────┘
robot (passe 3b) : touche `rdv` + pont `identites` (vid ↔ email, source calendly) + `attribution_rdv`
```

- **Make `7044512` « [Calendly] RDV -> Supabase rdv »** (team 2222349) : webhook custom `3587000`
  (`https://hook.eu1.make.com/pqa1rftko3f7d56viffsf21abel5c2zp`) → filtre `event` commence par
  `invitee.` → **JSON > Create JSON** (data structure `542690`, c'est lui qui échappe guillemets et
  retours à la ligne des réponses) → **Webhook response** (renvoie le JSON produit : un `curl` sur le
  hook montre le mapping) → **HTTP POST** `/rest/v1/rdv?on_conflict=invitee_uri` (upsert, clé
  publishable, policies `rdv_write`/`rdv_update`), erreur ignorée pour ne jamais désactiver le scénario.
  Colonnes : identité, créneau, `statut` (`pris`/`annule`), `cree_le` (la réservation), `vid`
  (`tracking.salesforce_uuid`), `utm_*` (`tracking.*`), `r1..r5` (réponses dans l'ordre des questions),
  `motif_annulation`, `raw`.
- **Abonner Calendly au hook** (pas d'UI chez Calendly, API seulement, plan Standard ou plus) :
  Calendly → Intégrations → API & Webhooks → *Personal access token*, puis
  ```bash
  TOKEN=… ; ME=$(curl -s https://api.calendly.com/users/me -H "Authorization: Bearer $TOKEN")
  ORG=$(echo "$ME" | python3 -c 'import sys,json;print(json.load(sys.stdin)["resource"]["current_organization"])')
  curl -s -X POST https://api.calendly.com/webhook_subscriptions -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" -d "{\"url\":\"https://hook.eu1.make.com/pqa1rftko3f7d56viffsf21abel5c2zp\",
    \"events\":[\"invitee.created\",\"invitee.canceled\"],\"organization\":\"$ORG\",\"scope\":\"organization\"}"
  ```
  Contrôle : `curl -s "https://api.calendly.com/webhook_subscriptions?organization=$ORG&scope=organization" -H "Authorization: Bearer $TOKEN"`
  → l'abonnement en `state: active`. Puis une vraie réservation test → ligne dans `rdv` sous 10 s,
  exécution visible dans l'historique Make.
- **Robot, passe 3b** (`run_rdv`, après l'attribution des ventes, `--recalc` la rejoue) : pour chaque
  RDV neuf ou mis à jour par Make (`maj_le` > `calcule_le`) → pont `identites` (le vid du snippet rejoint
  l'email de la réservation), touche `rdv` (id déterministe, datée de la réservation, visible dans
  **Parcours**), `attribution_rdv` (même fonction `attribuer` que les ventes, `tunnel = call`).
- **Écran Calls** (`attribution.html`) : RDV pris / annulés, closing (vente du même email APRÈS la
  réservation), CA des RDV closés, coût par RDV ads (dépense Meta de la période ÷ RDV attribués aux
  ads), table par canal, liste avec niveau (`r1`), blocage (`r2`), budget (`r5`).
- Limites : un RDV pris avec un email inconnu et sans `vid` (lien Calendly hors site, ex. envoyé en DM
  sans passer par `go.`) n'a pas de parcours → attribution `sio_contact` ou `aucune`, comme une vente.
  Pour les liens envoyés en DM, créer un lien court `go.lessecretsdegeoffrey.fr/<slug>` vers Calendly :
  le vid est posé au passage et le canal du lien est connu.

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
réelles, avec `--capi` seulement) · `--seulement-capi` (saute dépenses/contacts/attribution). Depuis GitHub → Actions →
« Attribution ventes » → Run workflow : champs `capi_test_code`, `capi_go`, `capi_retry`. Le
workflow a un groupe `concurrency` : un run manuel qui chevauche le cron attend son tour.

Tout refus de Meta (test OU réel) met le job en ROUGE (mail d'échec) : lire `capi_envois.reponse`
ou la ligne `ERREUR :` du log. Une ligne `JOURNAL KO apres envoi` = la vente est partie chez Meta
mais pas journalisée (Supabase KO) : le run suivant la renverra avec le même `event_id`, que Meta
dédoublonne pendant 48 h.
