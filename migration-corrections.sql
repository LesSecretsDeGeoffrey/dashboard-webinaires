-- ============================================
-- MIGRATION + CORRECTIONS — 10/06/2026
-- ============================================
-- À lancer UNE FOIS dans Supabase > SQL Editor (colle tout > Run).
-- (1) Crée les 5 colonnes funnel manquantes — sans elles, le formulaire
--     « + Nouveau webinaire » ÉCHOUE → indispensable AVANT le live du 14/06
-- (2) Corrige les données fausses du 24/05 et du 17/05
-- (3) Remplit vues/clics (Meta) pour les 5 lives
-- Sources : Meta compte 3739233859731846 (amount_spent/impressions/link clicks)
--           + WebinarJam (webinars 57 · 67 · 68 · 73 · 78), tirés le 10/06.
-- Aucune suppression — uniquement des ajouts de colonnes et des updates ciblés.
-- ============================================

-- (1) Migration : colonnes funnel
alter table public.webinaires add column if not exists vues int default 0;
alter table public.webinaires add column if not exists clics int default 0;
alter table public.webinaires add column if not exists presents_debut int default 0;
alter table public.webinaires add column if not exists clics_lien int default 0;
alter table public.webinaires add column if not exists tx_visionnage_live int default 0;

-- (2) Live 24/05 — corrections (réel : 7 ventes · 991 inscrits ads · 2 046 € de pub)
update public.webinaires set
  ventes_count = 7, ventes_montant_total = 3479,
  inscrits_ads = 991, ads_depense = 2046,
  vues = 295421, clics = 6197
where date = '2026-05-24';

-- Live 17/05 — corrections (réel WJ : 783 inscrits ads dont 362 ManyChat · Meta 11-17/05 : 750 €)
update public.webinaires set
  inscrits_ads = 783, ads_depense = 750,
  vues = 88958, clics = 2585
where date = '2026-05-17';

-- (3) Compléments vues/clics (les autres champs de ces lives étaient déjà justes)
update public.webinaires set vues = 149945, clics = 2886 where date = '2026-05-03';
update public.webinaires set vues = 299629, clics = 6438 where date = '2026-05-31';
update public.webinaires set vues = 296552, clics = 3615 where date = '2026-06-07';

-- ============================================
-- DONE — recharge le dashboard : formulaire débloqué + chiffres justes partout.
-- ============================================
