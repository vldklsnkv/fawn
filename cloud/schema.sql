-- Separate Fawn tables; do not change the existing Chel receiver or its data.
create table public.fawn_cloud_settings (
  id integer primary key check (id = 1),
  secret_sha256 text not null check (secret_sha256 ~ '^[a-f0-9]{64}$'),
  allowed_chat_ids text[] not null,
  enabled boolean not null default false
);
alter table public.fawn_cloud_settings enable row level security;
revoke all on public.fawn_cloud_settings from public, anon, authenticated;
grant select on public.fawn_cloud_settings to service_role;

create table public.fawn_cloud_inbox (
  update_id bigint primary key,
  message_id bigint not null,
  chat_id text not null,
  sent_at timestamptz not null,
  edited_at timestamptz,
  received_at timestamptz not null,
  source_text text not null,
  user_comment text not null,
  urls jsonb not null check (jsonb_typeof(urls) = 'array'),
  raw_update jsonb not null,
  diagnostic boolean not null default false
);
create index fawn_cloud_inbox_received on public.fawn_cloud_inbox(received_at, update_id);
alter table public.fawn_cloud_inbox enable row level security;
revoke all on public.fawn_cloud_inbox from public, anon, authenticated;
grant select, insert on public.fawn_cloud_inbox to service_role;
