-- ============================================
-- SEED FUNNEL LIBERTY — 3 derniers lives (68 · 73 · 78)
-- ============================================
-- À lancer dans Supabase > SQL Editor > New query, APRÈS supabase-schema.sql
-- (qui crée les 7 colonnes funnel).
--
-- Robuste : chaque live est mis à jour s'il existe déjà (UPDATE par date),
-- sinon il est créé (INSERT). Aucune dépendance à l'état actuel de la base.
--
-- Sources : Meta MCP (vues/clics/budget, compte 3739233859731846) + données live.
-- Méthode CPL/coût-acheteur = spend Meta ÷ inscrits WJ (jamais le pixel).
-- Ces valeurs sont les chiffres de référence ; ajuste si tu en as de plus fins.
-- inscrits_ads des lives 73/78 = estimation (à affiner via wj_cpl_7juin.py / wj_showup.py).
-- Champs manuels encore vides (à saisir au formulaire quand dispo) :
--   presents_debut · presents_whatsapp · messages_chat · clics_lien · replay_vues
-- ============================================

-- ===== LIVE 78 — dim. 07/06/2026 (V15, cold premium-LLA) =====
update public.webinaires set
  vues = 296552, clics = 3615, ads_depense = 2238,
  inscrits_total = 792, inscrits_ads = 760,
  presents_pic = 284, presents_pitch = 228,
  ventes_count = 6, ventes_montant_total = 2982
where date = '2026-06-07';

insert into public.webinaires
  (date, heure, titre, type, pitch_version, offre, vues, clics, ads_depense,
   inscrits_total, inscrits_ads, presents_pic, presents_pitch, ventes_count, ventes_montant_total)
select '2026-06-07', '18:00', 'Live 78 · Atelier Macarons', 'atelier', 'V15', 'MFP 497 €',
       296552, 3615, 2238, 792, 760, 284, 228, 6, 2982
where not exists (select 1 from public.webinaires where date = '2026-06-07');

-- ===== LIVE 73 — dim. 31/05/2026 (Fête des Mères, cash KO) =====
update public.webinaires set
  vues = 299629, clics = 6438, ads_depense = 2267,
  inscrits_total = 842, inscrits_ads = 800,
  presents_pic = 246, presents_pitch = 246,
  ventes_count = 1, ventes_montant_total = 497
where date = '2026-05-31';

insert into public.webinaires
  (date, heure, titre, type, pitch_version, offre, vues, clics, ads_depense,
   inscrits_total, inscrits_ads, presents_pic, presents_pitch, ventes_count, ventes_montant_total)
select '2026-05-31', '18:00', 'Live 73 · Atelier Macarons', 'atelier', 'V14', 'MFP 497 €',
       299629, 6438, 2267, 842, 800, 246, 246, 1, 497
where not exists (select 1 from public.webinaires where date = '2026-05-31');

-- ===== LIVE 68 — dim. 24/05/2026 (Pentecôte, 7 ventes) =====
update public.webinaires set
  vues = 295421, clics = 6197, ads_depense = 2046,
  inscrits_total = 991, inscrits_ads = 991,
  presents_pic = 292, presents_pitch = 292,
  ventes_count = 7, ventes_montant_total = 3479
where date = '2026-05-24';

insert into public.webinaires
  (date, heure, titre, type, pitch_version, offre, vues, clics, ads_depense,
   inscrits_total, inscrits_ads, presents_pic, presents_pitch, ventes_count, ventes_montant_total)
select '2026-05-24', '18:00', 'Live 68 · Atelier Macarons', 'atelier', 'V14', 'MFP 497 €',
       295421, 6197, 2046, 991, 991, 292, 292, 7, 3479
where not exists (select 1 from public.webinaires where date = '2026-05-24');

-- ============================================
-- DONE — Ouvre le dashboard > page "Funnel" pour voir le tunnel rempli + code couleur.
-- ============================================
