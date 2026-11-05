-- Website chatbot POC: initial schema.
-- Run in the Supabase SQL editor (or with the Supabase CLI). Requires pgvector.

create extension if not exists vector;
create extension if not exists pgcrypto;

-- ---------------------------------------------------------------------------
-- sites: one row per indexed website
create table if not exists sites (
    id            uuid primary key default gen_random_uuid(),
    root_url      text not null,
    status        text not null default 'queued'
                  check (status in ('queued', 'crawling', 'ready', 'failed')),
    pages_found   integer not null default 0,
    pages_done    integer not null default 0,
    chunk_count   integer not null default 0,
    error         text,
    contact_url   text,
    created_at    timestamptz not null default now()
);

-- pages: one row per fetched page (plus a synthetic "FAQ" page when provided)
create table if not exists pages (
    id          uuid primary key default gen_random_uuid(),
    site_id     uuid not null references sites(id) on delete cascade,
    url         text not null,
    title       text,
    fetched_at  timestamptz not null default now()
);
create index if not exists pages_site_id_idx on pages(site_id);

-- chunks: about 500 tokens each, with the embedding used for retrieval
create table if not exists chunks (
    id          uuid primary key default gen_random_uuid(),
    site_id     uuid not null references sites(id) on delete cascade,
    page_id     uuid references pages(id) on delete cascade,
    url         text not null,
    title       text,
    heading     text,
    content     text not null,
    token_count integer not null default 0,
    embedding   vector(1536) not null
);
create index if not exists chunks_site_id_idx on chunks(site_id);
create index if not exists chunks_embedding_hnsw_idx
    on chunks using hnsw (embedding vector_cosine_ops);

-- chat_sessions: the widget creates the uuid and keeps it in localStorage
create table if not exists chat_sessions (
    id          uuid primary key,
    site_id     uuid references sites(id) on delete cascade,
    ip_hash     text,
    created_at  timestamptz not null default now()
);

-- chat_messages: every user and assistant message with timings and cost
create table if not exists chat_messages (
    id              uuid primary key default gen_random_uuid(),
    session_id      uuid not null references chat_sessions(id) on delete cascade,
    role            text not null check (role in ('user', 'assistant')),
    content         text,
    condensed_query text,
    sources         jsonb,
    tokens_in       integer,
    tokens_out      integer,
    cost_usd        numeric(10, 6),
    retrieval_ms    integer,
    first_token_ms  integer,
    total_ms        integer,
    outcome         text check (outcome in ('answered', 'no_context', 'error', 'rate_limited')),
    created_at      timestamptz not null default now()
);
create index if not exists chat_messages_session_idx on chat_messages(session_id, created_at);
create index if not exists chat_messages_created_idx on chat_messages(created_at);

-- ---------------------------------------------------------------------------
-- Similarity search, filtered by site. Cosine distance operator is <=>.
create or replace function match_chunks(
    site_id         uuid,
    query_embedding vector(1536),
    match_count     int default 6,
    min_similarity  float default 0.3
)
returns table (
    id          uuid,
    page_id     uuid,
    url         text,
    title       text,
    heading     text,
    content     text,
    token_count integer,
    similarity  float
)
language sql
stable
as $$
    select
        c.id,
        c.page_id,
        c.url,
        c.title,
        c.heading,
        c.content,
        c.token_count,
        1 - (c.embedding <=> match_chunks.query_embedding) as similarity
    from chunks c
    where c.site_id = match_chunks.site_id
      and 1 - (c.embedding <=> match_chunks.query_embedding) >= match_chunks.min_similarity
    order by c.embedding <=> match_chunks.query_embedding
    limit match_chunks.match_count;
$$;

-- ---------------------------------------------------------------------------
-- Row level security: enabled everywhere, with no policies for anon.
-- Only the backend, using the service role key, can read or write.
alter table sites          enable row level security;
alter table pages          enable row level security;
alter table chunks         enable row level security;
alter table chat_sessions  enable row level security;
alter table chat_messages  enable row level security;

revoke all on function match_chunks(uuid, vector, int, float) from anon, authenticated;
