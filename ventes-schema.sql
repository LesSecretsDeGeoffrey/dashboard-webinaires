-- ============================================
-- TABLE VENTES — alimentée par le webhook « Nouvelle vente » Systeme.io
-- ============================================
-- À lancer dans Supabase > SQL Editor > New query (une fois).
-- Le récepteur Cloudflare Worker écrit ici à chaque vente ; le dashboard
-- attribue ensuite chaque vente au bon live par la date d'achat.
-- ============================================

create table if not exists public.ventes (
  id uuid primary key default gen_random_uuid(),
  email text,                 -- email acheteur (pour croiser avec les inscrits WJ)
  montant numeric,            -- montant réel payé (CA exact, plus d'estimation ×497)
  produit text,               -- nom du produit / offre
  purchased_at timestamptz,   -- DATE d'achat = la clé d'attribution au bon live
  webinaire_id uuid references public.webinaires(id) on delete set null, -- rempli à l'attribution
  raw jsonb,                  -- payload brut Systeme.io (pour affiner le parsing si besoin)
  created_at timestamptz default now()
);

create index if not exists idx_ventes_purchased_at on public.ventes(purchased_at desc);
create index if not exists idx_ventes_email on public.ventes(email);

-- RLS : même modèle que les autres tables (accès via la clé publishable, UI protégée par mot de passe)
alter table public.ventes enable row level security;
drop policy if exists "ventes_all" on public.ventes;
create policy "ventes_all" on public.ventes
  for all using (true) with check (true);

-- ============================================
-- DONE — la table est prête à recevoir les ventes du Worker.
-- ============================================
