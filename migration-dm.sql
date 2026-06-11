-- Pipeline hésitants automatique : colonnes pour la page DM Tracking.
-- À lancer UNE FOIS dans Supabase > SQL Editor > Run (4 lignes, aucun risque).
alter table public.dm_prospects add column if not exists email text;
alter table public.dm_prospects add column if not exists telephone text;
alter table public.dm_prospects add column if not exists time_live_min int default 0;
alter table public.dm_prospects add column if not exists source text default '';
