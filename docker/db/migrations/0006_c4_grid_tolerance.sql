-- 0006 (C4 fix) Grid: ignore sub-centimetre overhang when counting rows/columns.
--
-- A polygon that arrives as GeoJSON keeps ~9 decimals (about 0.1 mm), so a
-- 200 m side comes back as 200.0001 m. 0004 then added an 11th row and column
-- of 0.1 mm slivers (120 cells instead of 100). Now an overhang below 1 cm does
-- not start a new row/column; anything wider still gets its (clipped) cell.
-- Grids made with the old version are rebuilt below.

CREATE OR REPLACE FUNCTION aq_make_grid(p_area UUID)
RETURNS INTEGER LANGUAGE plpgsql AS $$
DECLARE
    v_srid  INTEGER;
    v_m     geometry;          -- the area in UTM metres
    v_s     DOUBLE PRECISION;  -- cell size
    v_tol   CONSTANT DOUBLE PRECISION := 0.01;   -- metres
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
    v_rows := GREATEST(1, ceil((ST_YMax(v_m) - ST_YMin(v_m) - v_tol) / v_s)::int);
    v_cols := GREATEST(1, ceil((ST_XMax(v_m) - ST_XMin(v_m) - v_tol) / v_s)::int);
    IF v_rows::bigint * v_cols > 250000 THEN
        RAISE EXCEPTION 'grid would have % x % cells; use a larger cell_size_m', v_rows, v_cols;
    END IF;

    DELETE FROM grid_cells WHERE area_id = p_area;
    -- the last row / column is stretched by the (< 1 cm) overhang so nothing is left out
    INSERT INTO grid_cells (area_id, cell_row, cell_col, geom)
    SELECT p_area, r, c, ST_Transform(ST_Multi(clip), 4326)
    FROM (
        SELECT r, c,
               ST_CollectionExtract(ST_Intersection(
                   ST_MakeEnvelope(
                       ST_XMin(v_m) + c * v_s,
                       ST_YMin(v_m) + r * v_s,
                       CASE WHEN c = v_cols - 1 THEN ST_XMax(v_m) + v_tol ELSE ST_XMin(v_m) + (c + 1) * v_s END,
                       CASE WHEN r = v_rows - 1 THEN ST_YMax(v_m) + v_tol ELSE ST_YMin(v_m) + (r + 1) * v_s END,
                       v_srid),
                   v_m), 3) AS clip
        FROM generate_series(0, v_rows - 1) AS r,
             generate_series(0, v_cols - 1) AS c
    ) g
    WHERE NOT ST_IsEmpty(clip) AND ST_Area(clip) > 1e-6;
    GET DIAGNOSTICS n = ROW_COUNT;
    RETURN n;
END $$;

-- rebuild grids made by the 0004 version
SELECT aq_make_grid(area_id) FROM search_area;
