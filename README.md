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
