-- jiuhe-election-agent knowledge base schema
-- All tables are plain SQLite; the FTS table powers knowledge/event search.

CREATE TABLE IF NOT EXISTS elections (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,            -- county_mayor | president | regional_legislator | party_list | lowland_indigenous | highland_indigenous
    year        INTEGER NOT NULL,
    date        TEXT NOT NULL,            -- ISO YYYY-MM-DD
    UNIQUE(kind, year)
);

-- One row per candidate per district level (county / township / village / national).
CREATE TABLE IF NOT EXISTS votes (
    election_id INTEGER NOT NULL REFERENCES elections(id),
    county      TEXT NOT NULL,            -- canonical county name, e.g. 臺北市
    district    TEXT NOT NULL DEFAULT '', -- legislator 選區, e.g. 臺北市第03選區; '' otherwise
    township    TEXT NOT NULL DEFAULT '', -- '' for county-level rows; '全國' election rows use county='全國'
    village     TEXT NOT NULL DEFAULT '', -- '' when township/county level
    cand_no     INTEGER NOT NULL,
    votes       INTEGER NOT NULL,
    share       REAL,                     -- percent, as published
    elected     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_votes ON votes(election_id, county, township);

CREATE TABLE IF NOT EXISTS candidates (
    election_id INTEGER NOT NULL REFERENCES elections(id),
    county      TEXT NOT NULL,
    district    TEXT NOT NULL DEFAULT '',
    cand_no     INTEGER NOT NULL,
    name        TEXT NOT NULL,
    party       TEXT NOT NULL,            -- resolved party name; '無黨籍' for independents
    elected     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_cand ON candidates(election_id, county);

-- Upcoming-race candidates: 2026 registered roster, 2028 prospective names from news.
CREATE TABLE IF NOT EXISTS race_candidates (
    cycle       TEXT NOT NULL DEFAULT '2026', -- e.g. '2026', '2028'
    county      TEXT NOT NULL,              -- '全國' for president / party list
    district    TEXT NOT NULL DEFAULT '',   -- legislator 選區, e.g. 臺北市第03選區
    name        TEXT NOT NULL,
    party       TEXT NOT NULL DEFAULT '',
    office      TEXT NOT NULL DEFAULT '',   -- mayor | councilor | ... | president | vice_president | legislator | indigenous_legislator
    status      TEXT NOT NULL DEFAULT 'declared', -- prospective | shortlisted | declared | nominated | registered | withdrawn
    incumbent   INTEGER NOT NULL DEFAULT 0,
    source      TEXT NOT NULL DEFAULT '',
    updated_at  TEXT NOT NULL DEFAULT '',
    UNIQUE(cycle, county, name, office)
);

CREATE TABLE IF NOT EXISTS polls (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    org           TEXT NOT NULL,          -- polling org, e.g. TVBS
    publish_date  TEXT NOT NULL DEFAULT '',
    fieldwork_end TEXT NOT NULL DEFAULT '',
    county        TEXT NOT NULL DEFAULT '',   -- '全國' for national polls
    district      TEXT NOT NULL DEFAULT '',
    race          TEXT NOT NULL DEFAULT 'county_mayor', -- county_mayor | president | legislator | party_list
    method        TEXT NOT NULL DEFAULT '',
    sample_n      INTEGER,
    results_json  TEXT NOT NULL DEFAULT '{}', -- {"候選人": pct, ...}
    source_url    TEXT NOT NULL DEFAULT '',
    raw_path      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_polls ON polls(county, publish_date);

-- Verifiable campaign events (real-time retrieval sinks here).
CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    county     TEXT NOT NULL DEFAULT '',
    date       TEXT NOT NULL DEFAULT '',
    kind       TEXT NOT NULL DEFAULT '',  -- rally | poll_release | scandal | endorsement | registration | other
    summary    TEXT NOT NULL,
    source_url TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_events ON events(county, date);

-- Local knowledge: factions, networks, issues — always time-scoped.
CREATE TABLE IF NOT EXISTS knowledge (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    county     TEXT NOT NULL DEFAULT '',
    topic      TEXT NOT NULL DEFAULT '',  -- faction | organization | issue | candidate_profile | other
    claim      TEXT NOT NULL,
    valid_from TEXT NOT NULL DEFAULT '',
    valid_to   TEXT NOT NULL DEFAULT '',
    source     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_knowledge ON knowledge(county, topic);

-- trigram tokenizer enables substring matching, required for CJK text
-- (unicode61 would treat a whole Chinese sentence as one token)
CREATE VIRTUAL TABLE IF NOT EXISTS kb_fts USING fts5(
    text,
    content_type,     -- knowledge | event
    row_id UNINDEXED,
    tokenize='trigram'
);
