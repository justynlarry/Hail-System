-- Address Search.  Geocoding comes from the Census Geocoder
-- API (same TIGER data the postgis_tiger_geocoder would use,
-- without a 64-county local load).  Two tables: 1. A cache so
-- an address is geocoded once, 2. a log of what people searched
-- for.

BEGIN;

CREATE TABLE geocode_cache (
    geocode_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- The standardized form is the key.  The APPLICATION MUST compute this
    -- as address_key(<typed input>) in SQL -- never reimplement the
    -- normalization in Python.  address_key() reads address_standardizer's
    -- reference tables; any approximation diverges silently, producing cache
    -- misses and duplicate rows rather than an error.
    address_key         TEXT            NOT NULL UNIQUE,
    query_raw           TEXT            NOT NULL,
    matched_address     TEXT            NOT NULL,
    latitude            NUMERIC(9,6)    NOT NULL,
    longitude           NUMERIC(9,6)    NOT NULL,
    geom                GEOMETRY(Point, 4326) GENERATED ALWAYS AS
                            (ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)) STORED,
    -- Census returns the TIGER edge ID (TLID) of the matched street segment,
    -- not a tract.  Keeping it makes a later jurisdiction join possible.
    tiger_line_id       TEXT,
    geocoded_at         TIMESTAMPTZ     NOT NULL DEFAULT now()
);

CREATE INDEX geocode_cache_geom_gix ON geocode_cache USING GIST (geom);

COMMENT ON TABLE geocode_cache IS
    'One row per successfully geocoded address, keyed on address_key(). '
    'Census data changes slowly, rows are not expired automatically.';

CREATE TABLE address_searches (
    search_id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    query_raw           TEXT        NOT NULL,
    -- NULL when no key could be built (unparseable, or no house number)
    address_key         TEXT,
    geocode_id          BIGINT
                            CONSTRAINT fk_address_searches_geocode
                            REFERENCES geocode_cache (geocode_id),
    outcome             TEXT        NOT NULL
                            CHECK (outcome IN ('matched', 'no_match',
                                                'unparseable', 'service_error')),
    reports_found       INTEGER,
    range_start         DATE,
    range_end           DATE,
    searched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    searched_by         BIGINT      NOT NULL
                            CONSTRAINT fk_address_searches_user
                            REFERENCES users (emp_id)
);

CREATE INDEX address_searches_searched_at_idx
    ON address_searches (searched_at DESC);

COMMENT ON TABLE address_searches IS
    'Every search including misses.  Failed searches are a '
    'signal for whether fuzzy suggestions are necessary.';

GRANT SELECT, INSERT ON geocode_cache, address_searches TO hail_app;

COMMIT;