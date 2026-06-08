-- ============================================
-- DASHBOARD WEBINAIRES — Schéma Supabase
-- ============================================
-- À exécuter dans Supabase Dashboard > SQL Editor > New query
-- Une seule fois. Crée 3 tables + politiques d'accès.
-- ============================================

-- ===== 1. TABLE WEBINAIRES =====
create table if not exists public.webinaires (
  id uuid primary key default gen_random_uuid(),
  date date not null,
  heure text default '18:00',
  titre text not null,
  type text default 'atelier',
  pitch_version text,
  offre text,
  inscrits_total int default 0,
  inscrits_ads int default 0,
  inscrits_organique int default 0,
  inscrits_email int default 0,
  presents_pic int default 0,
  presents_pitch int default 0,
  duree_pitch_min int default 0,
  ventes_count int default 0,
  ventes_montant_total int default 0,
  paiements_1x int default 0,
  paiements_3x int default 0,
  paiements_4x_paypal int default 0,
  ads_depense int default 0,
  -- Funnel Liberty (ajoutées 08/06) :
  vues int default 0,              -- impressions Meta
  clics int default 0,             -- clics sur le lien (link clicks)
  presents_debut int default 0,    -- présence début 3-5 min (sinon = pic présents)
  presents_whatsapp int default 0, -- présents sur WhatsApp
  messages_chat int default 0,     -- nombre de messages dans le chat
  clics_lien int default 0,        -- clics sur le lien pendant le live
  replay_vues int default 0,       -- vues du replay (monte sur plusieurs jours)
  notes_generales text default '',
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists idx_webinaires_date on public.webinaires(date desc);

-- ===== 1bis. MIGRATION FUNNEL LIBERTY (08/06) =====
-- Idempotent : ajoute les 7 colonnes funnel à une table déjà créée.
-- (le bloc create table ci-dessus ne s'applique PAS à une table existante)
alter table public.webinaires add column if not exists vues int default 0;
alter table public.webinaires add column if not exists clics int default 0;
alter table public.webinaires add column if not exists presents_debut int default 0;
alter table public.webinaires add column if not exists presents_whatsapp int default 0;
alter table public.webinaires add column if not exists messages_chat int default 0;
alter table public.webinaires add column if not exists clics_lien int default 0;
alter table public.webinaires add column if not exists replay_vues int default 0;

-- ===== 2. TABLE OBJECTIONS =====
create table if not exists public.objections (
  id uuid primary key default gen_random_uuid(),
  webinaire_id uuid not null references public.webinaires(id) on delete cascade,
  texte text not null,
  count int default 1,
  created_at timestamptz default now()
);

create index if not exists idx_objections_webinaire on public.objections(webinaire_id);

-- ===== 3. TABLE DM_PROSPECTS =====
create table if not exists public.dm_prospects (
  id uuid primary key default gen_random_uuid(),
  webinaire_id uuid not null references public.webinaires(id) on delete cascade,
  prenom text not null,
  notes text default '',
  status text default 'a_envoyer',
  date_relance date,
  created_at timestamptz default now(),
  updated_at timestamptz default now()
);

create index if not exists idx_dm_prospects_webinaire on public.dm_prospects(webinaire_id);
create index if not exists idx_dm_prospects_status on public.dm_prospects(status);

-- ===== 4. TRIGGERS updated_at =====
create or replace function public.set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

drop trigger if exists trg_webinaires_updated_at on public.webinaires;
create trigger trg_webinaires_updated_at
  before update on public.webinaires
  for each row execute function public.set_updated_at();

drop trigger if exists trg_dm_prospects_updated_at on public.dm_prospects;
create trigger trg_dm_prospects_updated_at
  before update on public.dm_prospects
  for each row execute function public.set_updated_at();

-- ===== 5. ROW LEVEL SECURITY =====
-- Single-user pour démarrer : on autorise l'accès complet via l'anon key.
-- Le password côté front protégera l'UI.
-- Si tu veux durcir plus tard, on passera sur de l'auth Supabase classique.

alter table public.webinaires enable row level security;
alter table public.objections enable row level security;
alter table public.dm_prospects enable row level security;

-- Webinaires : lecture/écriture full pour tout le monde (protégé par password côté front)
drop policy if exists "webinaires_all" on public.webinaires;
create policy "webinaires_all" on public.webinaires
  for all using (true) with check (true);

-- Objections : idem
drop policy if exists "objections_all" on public.objections;
create policy "objections_all" on public.objections
  for all using (true) with check (true);

-- DM prospects : idem
drop policy if exists "dm_prospects_all" on public.dm_prospects;
create policy "dm_prospects_all" on public.dm_prospects
  for all using (true) with check (true);

-- ============================================
-- DONE — Vérifier dans Table Editor que les 3 tables sont créées.
-- ============================================
