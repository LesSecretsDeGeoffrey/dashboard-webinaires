-- Colonne du show-up prévisionnel (remplie automatiquement par le robot WebinarJam).
-- À lancer UNE FOIS dans Supabase > SQL Editor > Run (1 ligne, aucun risque).
alter table public.webinaires add column if not exists showup_previsionnel int default 0;
