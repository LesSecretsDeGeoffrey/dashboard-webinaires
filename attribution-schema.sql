-- ============================================================
-- TRACKING & ATTRIBUTION (phase 1) : jouer UNE fois dans
-- Supabase > SQL Editor > New query. Idempotent, rejouable.
-- Spec : docs/superpowers/specs/2026-08-19-tracking-attribution-design.md
-- Rien ici ne touche aux tables existantes (ventes, webinaires...).
-- ============================================================

-- Etat interne du robot (curseur contacts SIO, etc.)
create table if not exists public.sync_state (
  key text primary key,
  value jsonb,
  updated_at timestamptz default now()
);

-- Une ligne par navigateur identifie (rempli en phase 2 par Cloudflare Pages)
create table if not exists public.visiteurs (
  vid text primary key,
  first_seen timestamptz default now(),
  last_seen timestamptz,
  premier_contact jsonb,
  pays text,
  device text
);

-- SOURCE DE VERITE des evenements. Phase 1 : seul le robot y ecrit (type='achat').
create table if not exists public.touches (
  id uuid primary key default gen_random_uuid(),
  vid text,
  ts timestamptz not null default now(),
  type text not null check (type in ('pageview','click_go','identite','achat')),
  url text,
  path text,
  referrer text,
  utm_source text, utm_medium text, utm_campaign text, utm_term text, utm_content text,
  utm_id text,          -- id de pub Meta si present dans l'URL
  fbclid text, fbc text, fbp text,
  slug text,            -- lien court a l'origine du clic
  email text,           -- minuscules ; types identite/achat seulement
  contexte text,        -- 'optin' | 'checkout' pour type='identite'
  ip_pays text, ua text, ip text,
  extra jsonb
);
create index if not exists idx_touches_vid_ts on public.touches(vid, ts);
create index if not exists idx_touches_email on public.touches(email);
create index if not exists idx_touches_type_ts on public.touches(type, ts desc);

-- Pont cross-device : un email peut couvrir plusieurs vid
create table if not exists public.identites (
  vid text not null,
  email text not null,
  first_seen_at timestamptz default now(),
  source text check (source in ('optin','checkout','vente')),
  primary key (vid, email)
);
create index if not exists idx_identites_email on public.identites(email);

-- Liens courts go.lessecretsdegeoffrey.fr/<slug> (utilises en phase 2)
create table if not exists public.liens (
  slug text primary key,
  destination text not null,
  canal text not null default 'autre'
    check (canal in ('manychat','email','story','bio','whatsapp','ads','autre')),
  libelle text,
  clics int default 0,
  actif boolean default true,
  created_at timestamptz default now()
);

-- Copie locale des contacts Systeme.io (champs utm de l'inscription)
create table if not exists public.contacts_sio (
  contact_id text primary key,
  email text,
  registered_at timestamptz,
  utm_source text, utm_medium text, utm_campaign text, utm_term text, utm_content text,
  fbclid text, fbc text,
  tags text[] default '{}',
  synced_at timestamptz default now()
);
create index if not exists idx_contacts_sio_email on public.contacts_sio(email);
create index if not exists idx_contacts_sio_registered on public.contacts_sio(registered_at);
create index if not exists idx_contacts_sio_tags on public.contacts_sio using gin(tags);

-- Depense Meta PAR PUB et PAR JOUR (le robot rejoue J-3 a J a chaque passage)
create table if not exists public.depenses_ads (
  date date not null,
  ad_id text not null,
  campaign_id text, campaign_name text,
  adset_id text, adset_name text,
  ad_name text,
  slug_crea text,       -- ad_name avant le premier ' | ' (convention create_campagne_*.py)
  spend numeric default 0,
  impressions int default 0,
  clicks int default 0,
  synced_at timestamptz default now(),
  primary key (date, ad_id)
);
create index if not exists idx_depenses_slug on public.depenses_ads(slug_crea, adset_name);

-- Une ligne par vente. DERIVEE : recalculable a volonte depuis touches/contacts_sio.
create table if not exists public.attribution (
  vente_id uuid primary key references public.ventes(id) on delete cascade,
  email text,
  vid text,
  modele text check (modele in ('last_paid','first','sio_contact','aucune')),
  premier_contact jsonb,
  dernier_contact jsonb,          -- tous canaux (l'ecran Canaux lit son canal)
  dernier_contact_payant jsonb,
  canal text,                     -- canal de la touche retenue par le modele (ecran Pubs)
  canal_dernier text,             -- canal de dernier_contact (ecran Canaux)
  tunnel text,
  ad_id text, slug_crea text, adset_name text, campaign_name text,
  nb_touches int default 0,
  delai_j numeric,
  calcule_le timestamptz default now()
);

-- Journal des Purchase envoyes a Meta (phase 3)
create table if not exists public.capi_envois (
  vente_id uuid primary key references public.ventes(id) on delete cascade,
  event_id text,
  sent_at timestamptz default now(),
  statut text,
  reponse jsonb,
  test boolean default false
);

-- ============================================================
-- canal_de : LA definition du canal. Copie Python dans
-- attribution_sync.py, verrouillee par un test de parite.
-- ============================================================
create or replace function public.canal_de(src text, med text) returns text
language sql immutable as $$
  select case
    when lower(trim(coalesce(med,''))) = 'paid'
      or lower(trim(coalesce(src,''))) in ('fb','ig','facebook','instagram','an','msg','meta','ads')
      then 'ads'
    when lower(trim(coalesce(src,''))) in ('manychat','email','story','bio','whatsapp')
      then lower(trim(src))
    when lower(trim(coalesce(med,''))) like '%manychat%' then 'manychat'
    when lower(trim(coalesce(med,''))) = 'lien' then 'lien'
    else 'organique'
  end
$$;

-- ============================================================
-- RLS : lecture pour la cle publishable (UI protegee par mdp),
-- ecriture reservee a la cle service (le service role CONTOURNE
-- la RLS, donc aucune policy d'ecriture n'est necessaire).
-- Exception : liens, que le front pourra creer (phase 2) ; pas de mise a jour par la cle publishable.
-- ============================================================
do $$
declare t text;
begin
  foreach t in array array['sync_state','visiteurs','touches','identites',
                           'contacts_sio','depenses_ads','attribution','capi_envois']
  loop
    execute format('alter table public.%I enable row level security', t);
    execute format('drop policy if exists "%s_read" on public.%I', t, t);
    execute format('create policy "%s_read" on public.%I for select using (true)', t, t);
  end loop;
end $$;

alter table public.liens enable row level security;
drop policy if exists "liens_read"   on public.liens;
drop policy if exists "liens_insert" on public.liens;
drop policy if exists "liens_update" on public.liens;
create policy "liens_read"   on public.liens for select using (true);
create policy "liens_insert" on public.liens for insert with check (true);

-- ============================================================
-- VUES (une requete par ecran ; agregats PAR JOUR, le front somme)
-- Dates en heure de Paris.
-- ============================================================
create or replace view public.v_pubs as
with dep as (
  select date,
         coalesce(nullif(trim(adset_name), ''), '?') as adset_name,
         coalesce(nullif(trim(slug_crea), ''), '?')  as slug_crea,
         sum(spend) as spend, sum(impressions) as impressions, sum(clicks) as clicks
  from public.depenses_ads
  group by 1, 2, 3
), ins as (
  select (registered_at at time zone 'Europe/Paris')::date as date,
         coalesce(nullif(trim(utm_term), ''), '?')    as adset_name,
         coalesce(nullif(trim(utm_content), ''), '?') as slug_crea,
         count(*) as inscrits
  from public.contacts_sio
  where utm_content is not null
  group by 1, 2, 3
), ven as (
  select (v.purchased_at at time zone 'Europe/Paris')::date as date,
         coalesce(nullif(trim(a.adset_name), ''), '?') as adset_name,
         coalesce(nullif(trim(a.slug_crea), ''), '?')  as slug_crea,
         count(*) as ventes, sum(coalesce(v.montant, 0)) as ca
  from public.attribution a
  join public.ventes v on v.id = a.vente_id
  where a.slug_crea is not null
  group by 1, 2, 3
)
select coalesce(dep.date, ins.date, ven.date)                    as date,
       coalesce(dep.adset_name, ins.adset_name, ven.adset_name)  as adset_name,
       coalesce(dep.slug_crea,  ins.slug_crea,  ven.slug_crea)   as slug_crea,
       coalesce(dep.spend, 0) as spend,
       coalesce(dep.impressions, 0) as impressions,
       coalesce(dep.clicks, 0) as clicks,
       coalesce(ins.inscrits, 0) as inscrits,
       coalesce(ven.ventes, 0) as ventes,
       coalesce(ven.ca, 0) as ca
from dep
full join ins using (date, adset_name, slug_crea)
full join ven using (date, adset_name, slug_crea);

-- Ventes par canal (ecran Canaux) : canal_dernier, ligne 'non attribue' incluse
create or replace view public.v_canaux as
select (v.purchased_at at time zone 'Europe/Paris')::date as date,
       coalesce(a.canal_dernier, 'non attribue') as canal,
       count(*) as ventes,
       sum(coalesce(v.montant, 0)) as ca
from public.ventes v
left join public.attribution a on a.vente_id = v.id
group by 1, 2;

-- Inscrits par canal (meme definition canal_de que le robot)
create or replace view public.v_canaux_inscrits as
select (registered_at at time zone 'Europe/Paris')::date as date,
       public.canal_de(utm_source, utm_medium) as canal,
       count(*) as inscrits
from public.contacts_sio
group by 1, 2;

-- Contacts avec canal calcule + tags : le filtre 'live' du front compte les
-- inscrits par APPARTENANCE AU TAG (regle /cpl, spec §7.2), pas par date.
-- Evite une 3e copie de canal_de en JavaScript.
create or replace view public.v_contacts_pub as
select contact_id, tags, utm_term, utm_content,
       public.canal_de(utm_source, utm_medium) as canal,
       (registered_at at time zone 'Europe/Paris')::date as date
from public.contacts_sio;

-- ============================================================
-- PHASE 2 : fonctions appelees par Cloudflare Pages + vues ecrans.
-- Rejouer le fichier ENTIER (idempotent, comme en phase 1).
-- ============================================================

-- Upsert atomique du visiteur : premier_contact FIGE au premier passage,
-- last_seen rafraichi ensuite (2 requetes REST seraient une course).
create or replace function public.upsert_visiteur(
  p_vid text, p_premier jsonb, p_pays text, p_device text) returns void
language sql as $$
  insert into public.visiteurs (vid, first_seen, last_seen, premier_contact, pays, device)
  values (p_vid, now(), now(), p_premier, p_pays, p_device)
  on conflict (vid) do update
    set last_seen = now(),
        pays   = coalesce(public.visiteurs.pays,   excluded.pays),
        device = coalesce(public.visiteurs.device, excluded.device);
$$;

-- Compteur de clics d'un lien court (increment atomique)
create or replace function public.clic_lien(p_slug text) returns void
language sql as $$
  update public.liens set clics = coalesce(clics, 0) + 1 where slug = p_slug;
$$;

-- Ces deux fonctions ECRIVENT : reservees a la cle service (Cloudflare Pages).
-- Sans ce revoke, la cle publishable du front pourrait les appeler.
revoke execute on function public.upsert_visiteur(text, jsonb, text, text)
  from public, anon, authenticated;
revoke execute on function public.clic_lien(text) from public, anon, authenticated;
grant execute on function public.upsert_visiteur(text, jsonb, text, text) to service_role;
grant execute on function public.clic_lien(text) to service_role;

-- Tunnel d'un path (ecran Tunnels). SURENSEMBLE des fragments TUNNELS_PATH
-- d'attribution_sync.py (test de parite pytest : le SQL peut en savoir plus,
-- jamais moins). Les paths inconnus restent visibles en 'autre'.
create or replace function public.tunnel_de_path(p text) returns text
language sql immutable as $$
  select case
    -- ordre significatif : les pages call (…pro997, …pro697) vivent sous le
    -- prefixe /paiementfondationspro, elles passent AVANT le generique 'live'
    -- (meme ordre que TUNNELS_PATH dans attribution_sync.py)
    when lower(p) like '%/paiementfondationspro997%'
      or lower(p) like '%/paiementfondationspro697%'
      or lower(p) like '%/paiementmethode997%'                                then 'call'
    when lower(p) like '%/paiementfondationspro%' or lower(p) like '%/live2%' then 'live'
    when lower(p) like '%/paiementbook%' or lower(p) like '%/maitrise%'       then 'ebook'
    else 'autre'
  end
$$;

-- Ecran Tunnels : personnes distinctes (vid) par path, par tunnel
create or replace view public.v_tunnel_etapes as
select public.tunnel_de_path(path) as tunnel, path,
       count(distinct vid) as personnes, count(*) as vues
from public.touches
where type = 'pageview' and path is not null and vid is not null
group by 1, 2;

-- Ecran Canaux : clics de liens courts par canal et par jour
create or replace view public.v_clics_liens as
select (t.ts at time zone 'Europe/Paris')::date as date,
       coalesce(l.canal, 'autre') as canal,
       count(*) as clics
from public.touches t
left join public.liens l on l.slug = t.slug
where t.type = 'click_go'
group by 1, 2;

-- ============================================================
-- VERIFICATION (a executer apres, doit rendre des lignes sans erreur)
-- ============================================================
-- select public.canal_de('fb', null);            -- 'ads'
-- select public.canal_de('manychat', 'lien');    -- 'manychat'
-- select public.canal_de('autre', 'lien');       -- 'lien'
-- select public.canal_de(null, null);            -- 'organique'
-- select * from public.v_pubs limit 1;
-- select * from public.v_canaux limit 5;
-- select public.tunnel_de_path('/paiementbook-direct');  -- 'ebook'
-- select public.tunnel_de_path('/live2');                -- 'live'
-- select * from public.v_tunnel_etapes limit 5;
-- select * from public.v_clics_liens limit 5;
-- select public.clic_lien('slug-inexistant');            -- rend void, 0 ligne touchee
