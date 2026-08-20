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
