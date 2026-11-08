-- 0005 (C5) Merge repeated detections of the same person into one pin.
--
-- survivors = one row per geolocated detection (many per person: every frame,
-- every drone that flies over). persons = one row per real person (the pin on
-- the dashboard). A detection joins the nearest person of the same mission
-- within p_radius_m (default 3 m); otherwise it starts a new person.
-- Ingestion calls aq_merge_survivors() right after it stores new survivors.

CREATE TABLE IF NOT EXISTS persons (
    person_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    mission_id    UUID NOT NULL REFERENCES missions (mission_id) ON DELETE CASCADE,
    lat           DOUBLE PRECISION NOT NULL,     -- confidence-weighted mean of its detections
    lon           DOUBLE PRECISION NOT NULL,
    n_detections  INTEGER NOT NULL DEFAULT 0,
    max_conf      REAL,
    first_ts      TIMESTAMPTZ,
    last_ts       TIMESTAMPTZ,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK (status IN ('pending', 'confirmed', 'rejected')),   -- set by the operator
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    geom          geometry(Point, 4326)
                  GENERATED ALWAYS AS (ST_SetSRID(ST_MakePoint(lon, lat), 4326)) STORED
);
CREATE INDEX IF NOT EXISTS persons_mission_idx ON persons (mission_id);
CREATE INDEX IF NOT EXISTS persons_geom_idx    ON persons USING GIST (geom);
-- ST_DWithin on geography (metres anywhere on Earth) uses this one
CREATE INDEX IF NOT EXISTS persons_geog_idx    ON persons USING GIST ((geom::geography));

ALTER TABLE survivors ADD COLUMN IF NOT EXISTS person_id UUID
    REFERENCES persons (person_id) ON DELETE SET NULL;
CREATE INDEX IF NOT EXISTS survivors_person_idx   ON survivors (person_id);
CREATE INDEX IF NOT EXISTS survivors_unmerged_idx ON survivors (mission_id, ts) WHERE person_id IS NULL;

-- Assign every not-yet-merged detection of a mission, oldest first.
-- Returns how many detections were assigned.
CREATE OR REPLACE FUNCTION aq_merge_survivors(p_mission UUID, p_radius_m DOUBLE PRECISION DEFAULT 3.0)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    s        RECORD;
    v_person UUID;
    n        INTEGER := 0;
BEGIN
    -- one merge per mission at a time
    PERFORM pg_advisory_xact_lock(7270002, hashtext(p_mission::text));

    FOR s IN
        SELECT sv.detection_id, sv.lat, sv.lon, sv.conf, sv.ts, sv.geom
          FROM survivors sv
         WHERE sv.mission_id = p_mission AND sv.person_id IS NULL
         ORDER BY sv.ts, sv.detection_id
    LOOP
        SELECT p.person_id INTO v_person
          FROM persons p
         WHERE p.mission_id = p_mission
           AND ST_DWithin(p.geom::geography, s.geom::geography, p_radius_m)
         ORDER BY ST_Distance(p.geom::geography, s.geom::geography)
         LIMIT 1;

        IF v_person IS NULL THEN
            INSERT INTO persons (mission_id, lat, lon, n_detections, max_conf, first_ts, last_ts)
            VALUES (p_mission, s.lat, s.lon, 1, s.conf, s.ts, s.ts)
            RETURNING person_id INTO v_person;
            UPDATE survivors SET person_id = v_person WHERE detection_id = s.detection_id;
        ELSE
            UPDATE survivors SET person_id = v_person WHERE detection_id = s.detection_id;
            UPDATE persons p
               SET (lat, lon, n_detections, max_conf, first_ts, last_ts, updated_at) =
                   (SELECT sum(m.lat * m.w) / sum(m.w), sum(m.lon * m.w) / sum(m.w),
                           count(*), max(m.conf), min(m.ts), max(m.ts), now()
                      FROM (SELECT sv.lat, sv.lon, sv.conf, sv.ts,
                                   GREATEST(COALESCE(sv.conf, 0), 0.01) AS w
                              FROM survivors sv WHERE sv.person_id = v_person) m)
             WHERE p.person_id = v_person;
        END IF;
        n := n + 1;
    END LOOP;
    RETURN n;
END $$;

-- Start the merge of one mission again (e.g. to try another radius).
-- Operator decisions (status) of that mission's persons are lost.
CREATE OR REPLACE FUNCTION aq_remerge_survivors(p_mission UUID, p_radius_m DOUBLE PRECISION DEFAULT 3.0)
RETURNS INTEGER LANGUAGE plpgsql AS $$
BEGIN
    UPDATE survivors SET person_id = NULL WHERE mission_id = p_mission;
    DELETE FROM persons WHERE mission_id = p_mission;
    RETURN aq_merge_survivors(p_mission, p_radius_m);
END $$;

-- One pin per person for the dashboard (E2/E3): the best evidence image is the
-- snapshot of its most confident detection.
CREATE OR REPLACE VIEW survivor_pins AS
SELECT p.person_id, p.mission_id, p.lat, p.lon, p.geom, p.n_detections, p.max_conf,
       p.first_ts, p.last_ts, p.status,
       (SELECT sv.snapshot_path FROM survivors sv
         WHERE sv.person_id = p.person_id AND sv.snapshot_path IS NOT NULL
         ORDER BY sv.conf DESC NULLS LAST, sv.ts DESC LIMIT 1) AS best_snapshot
FROM persons p;
