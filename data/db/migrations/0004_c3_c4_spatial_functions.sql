-- 0004 (C3, C4) Coordinate systems, search grid and coverage.
--
-- C3: everything is STORED as EPSG:4326 (lon/lat). Areas and distances are
-- COMPUTED in UTM. In Thailand (lon 96-102 E) that is zone 47N = EPSG:32647, as
-- agreed. aq_utm_srid() picks the zone from the geometry itself, so the same
-- functions also give correct metres in Gazebo worlds placed elsewhere (the PX4
-- default world is in Zurich = zone 32N; there 32647 would be badly wrong).

CREATE OR REPLACE FUNCTION aq_utm_srid(g geometry)
RETURNS INTEGER LANGUAGE sql IMMUTABLE STRICT PARALLEL SAFE AS $$
    SELECT CASE WHEN ST_Y(c) >= 0 THEN 32600 ELSE 32700 END
           + LEAST(60, GREATEST(1, floor((ST_X(c) + 180) / 6)::int + 1))
    FROM (SELECT ST_Centroid(ST_Transform(g, 4326)) AS c) s
$$;
COMMENT ON FUNCTION aq_utm_srid(geometry) IS
    'UTM EPSG code for the centroid of g (32647 in Thailand). Use it for areas and distances in metres (C3).';

-- Grid ----------------------------------------------------------------------------
-- Square cells of search_area.cell_size_m metres, aligned to the south-west
-- corner of the area's bounding box in UTM, clipped to the polygon.
CREATE OR REPLACE FUNCTION aq_make_grid(p_area UUID)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    v_srid  INTEGER;
    v_m     geometry;          -- the area in UTM metres
    v_s     DOUBLE PRECISION;  -- cell size
    v_rows  INTEGER;
    v_cols  INTEGER;
    n       INTEGER;
BEGIN
    SELECT aq_utm_srid(geom), ST_Transform(geom, aq_utm_srid(geom)), cell_size_m
      INTO v_srid, v_m, v_s
      FROM search_area WHERE area_id = p_area;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'search_area % not found', p_area;
    END IF;
    -- the small tolerance stops a 200.0000001 m side from making an 11th row of slivers
    v_rows := GREATEST(1, ceil((ST_YMax(v_m) - ST_YMin(v_m)) / v_s - 1e-6)::int);
    v_cols := GREATEST(1, ceil((ST_XMax(v_m) - ST_XMin(v_m)) / v_s - 1e-6)::int);
    IF v_rows::bigint * v_cols > 250000 THEN
        RAISE EXCEPTION 'grid would have % x % cells; use a larger cell_size_m', v_rows, v_cols;
    END IF;

    DELETE FROM grid_cells WHERE area_id = p_area;
    INSERT INTO grid_cells (area_id, cell_row, cell_col, geom)
    SELECT p_area, r, c, ST_Transform(ST_Multi(clip), 4326)
    FROM (
        SELECT r, c,
               ST_CollectionExtract(ST_Intersection(
                   ST_MakeEnvelope(ST_XMin(v_m) + c * v_s,       ST_YMin(v_m) + r * v_s,
                                   ST_XMin(v_m) + (c + 1) * v_s, ST_YMin(v_m) + (r + 1) * v_s, v_srid),
                   v_m), 3) AS clip
        FROM generate_series(0, v_rows - 1) AS r,
             generate_series(0, v_cols - 1) AS c
    ) g
    WHERE NOT ST_IsEmpty(clip) AND ST_Area(clip) > 1e-6;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;

-- One call for the planner (Nitis): store a search area from GeoJSON and build its grid.
-- p_geojson: a GeoJSON Polygon geometry, or a Feature holding one, in lon/lat (WGS84).
CREATE OR REPLACE FUNCTION aq_create_search_area(
    p_mission     UUID,
    p_geojson     TEXT,
    p_name        TEXT DEFAULT NULL,
    p_cell_size_m REAL DEFAULT 20)
RETURNS UUID LANGUAGE plpgsql AS $$
DECLARE
    v_json jsonb := p_geojson::jsonb;
    v_geom geometry;
    v_id   UUID;
BEGIN
    IF v_json ? 'geometry' THEN
        v_json := v_json -> 'geometry';
    END IF;
    v_geom := ST_SetSRID(ST_GeomFromGeoJSON(v_json::text), 4326);
    IF GeometryType(v_geom) = 'MULTIPOLYGON' AND ST_NumGeometries(v_geom) = 1 THEN
        v_geom := ST_GeometryN(v_geom, 1);
    END IF;
    IF GeometryType(v_geom) <> 'POLYGON' THEN
        RAISE EXCEPTION 'search area must be one Polygon, got %', GeometryType(v_geom);
    END IF;
    INSERT INTO missions (mission_id, status) VALUES (p_mission, 'planned')
        ON CONFLICT (mission_id) DO NOTHING;
    INSERT INTO search_area (mission_id, name, cell_size_m, geom)
        VALUES (p_mission, p_name, p_cell_size_m, v_geom)
        RETURNING area_id INTO v_id;
    PERFORM aq_make_grid(v_id);
    RETURN v_id;
END $$;

-- Flown paths, built live from telemetry ---------------------------------------
-- One line per drone, points in time order. Samples below p_min_alt_m (on the
-- ground, take-off) are left out. Also used by the Geomap in Phase D2.
CREATE OR REPLACE FUNCTION aq_flown_paths(
    p_mission   UUID,
    p_min_alt_m REAL DEFAULT 2,
    p_from      TIMESTAMPTZ DEFAULT NULL,
    p_to        TIMESTAMPTZ DEFAULT NULL)
RETURNS TABLE (drone_id TEXT, points BIGINT, first_ts TIMESTAMPTZ, last_ts TIMESTAMPTZ, geom geometry)
LANGUAGE sql STABLE AS $$
    SELECT t.drone_id, count(*), min(t.ts), max(t.ts), ST_MakeLine(t.geom ORDER BY t.ts)
    FROM telemetry t
    WHERE t.mission_id = p_mission
      AND (t.alt_rel IS NULL OR t.alt_rel >= p_min_alt_m)
      AND (p_from IS NULL OR t.ts >= p_from)
      AND (p_to   IS NULL OR t.ts <= p_to)
    GROUP BY t.drone_id
    HAVING count(*) >= 2
$$;

-- Keep the flown paths after a mission (kind = 'flown', next version per drone).
CREATE OR REPLACE FUNCTION aq_save_flown_paths(p_mission UUID, p_min_alt_m REAL DEFAULT 2)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE n INTEGER;
BEGIN
    INSERT INTO drones (drone_id) SELECT f.drone_id FROM aq_flown_paths(p_mission, p_min_alt_m) f
        ON CONFLICT (drone_id) DO NOTHING;
    INSERT INTO flight_paths (mission_id, drone_id, kind, version, geom)
    SELECT p_mission, f.drone_id, 'flown',
           COALESCE((SELECT max(version) FROM flight_paths q
                     WHERE q.mission_id = p_mission AND q.drone_id = f.drone_id AND q.kind = 'flown'), 0) + 1,
           f.geom
    FROM aq_flown_paths(p_mission, p_min_alt_m) f;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;

-- C4 Coverage ------------------------------------------------------------------
-- coverage % = area of the grid cells touched by the paths / area of the search area.
--   p_source  'flown'   live, from telemetry (default)
--             'planned' latest planned version per drone in flight_paths (mCPP)
--             'saved'   latest saved flown version per drone in flight_paths
--   p_swath_m 0 = a cell counts when the path line crosses it (as in the plan, C4);
--             > 0 = the path is widened to the camera footprint width first.
-- The paths of all drones are merged with ST_Union, so a cell seen by two drones
-- counts once. Cells never overlap, so their areas are simply added up.
CREATE OR REPLACE FUNCTION aq_coverage(
    p_area      UUID,
    p_source    TEXT DEFAULT 'flown',
    p_swath_m   DOUBLE PRECISION DEFAULT 0,
    p_min_alt_m REAL DEFAULT 2)
RETURNS TABLE (area_m2 DOUBLE PRECISION, covered_m2 DOUBLE PRECISION, coverage_pct DOUBLE PRECISION,
               cells_total INTEGER, cells_covered INTEGER)
LANGUAGE plpgsql STABLE AS $$
DECLARE
    v_srid    INTEGER;
    v_area    geometry;
    v_mission UUID;
    v_kind    TEXT;
    v_foot    geometry;
BEGIN
    SELECT aq_utm_srid(a.geom), ST_Transform(a.geom, aq_utm_srid(a.geom)), a.mission_id
      INTO v_srid, v_area, v_mission
      FROM search_area a WHERE a.area_id = p_area;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'search_area % not found', p_area;
    END IF;

    IF p_source = 'flown' THEN
        SELECT ST_Union(ST_Transform(f.geom, v_srid)) INTO v_foot
          FROM aq_flown_paths(v_mission, p_min_alt_m) f;
    ELSIF p_source IN ('planned', 'saved') THEN
        v_kind := CASE p_source WHEN 'planned' THEN 'planned' ELSE 'flown' END;
        SELECT ST_Union(ST_Transform(fp.geom, v_srid)) INTO v_foot
          FROM flight_paths fp
         WHERE fp.mission_id = v_mission AND fp.kind = v_kind
           AND fp.version = (SELECT max(q.version) FROM flight_paths q
                              WHERE q.mission_id = fp.mission_id AND q.drone_id = fp.drone_id AND q.kind = v_kind);
    ELSE
        RAISE EXCEPTION 'p_source must be flown, planned or saved (got %)', p_source;
    END IF;

    IF v_foot IS NOT NULL AND p_swath_m > 0 THEN
        v_foot := ST_Buffer(v_foot, p_swath_m / 2);
    END IF;
    v_foot := ST_Transform(v_foot, 4326);       -- back to 4326 so the GiST index on grid_cells is used

    RETURN QUERY
    SELECT ST_Area(v_area),
           cov.m2,
           CASE WHEN ST_Area(v_area) > 0 THEN round((100 * cov.m2 / ST_Area(v_area))::numeric, 2)::double precision END,
           (SELECT count(*)::int FROM grid_cells g WHERE g.area_id = p_area),
           cov.n
    FROM (SELECT COALESCE(sum(ST_Area(ST_Transform(c.geom, v_srid))), 0)::double precision AS m2,
                 count(*)::int AS n
            FROM grid_cells c
           WHERE c.area_id = p_area
             AND v_foot IS NOT NULL
             AND ST_Intersects(c.geom, v_foot)) cov;
END $$;
