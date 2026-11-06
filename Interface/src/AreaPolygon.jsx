import { useEffect, useRef } from "react";
import { useMap } from "@vis.gl/react-google-maps";

const toPoints = (path) => path.getArray().map((p) => ({ lat: p.lat(), lng: p.lng() }));

// Editable polygon: drag a vertex to move it, right-click a vertex to delete it,
// click inside the polygon to add a point (the map itself doesn't get those clicks).
export default function AreaPolygon({ points, onChange }) {
  const map = useMap();
  const poly = useRef(null);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  useEffect(() => {
    if (!map) return;
    const p = new google.maps.Polygon({
      map,
      editable: true,
      strokeColor: "#e53935",
      fillColor: "#e53935",
      fillOpacity: 0.2,
    });
    p.addListener("click", (e) => {
      if (e.vertex == null && e.edge == null) onChangeRef.current([...toPoints(p.getPath()), e.latLng.toJSON()]);
    });
    p.addListener("contextmenu", (e) => {
      if (e.vertex != null) onChangeRef.current(toPoints(p.getPath()).filter((_, i) => i !== e.vertex));
    });
    poly.current = p;
    return () => p.setMap(null);
  }, [map]);

  useEffect(() => {
    const p = poly.current;
    if (!p) return;
    p.setPath(points);
    const path = p.getPath();
    const update = () => onChangeRef.current(toPoints(path));
    const listeners = ["insert_at", "set_at", "remove_at"].map((ev) => path.addListener(ev, update));
    return () => listeners.forEach((l) => l.remove());
  }, [points, map]);

  return null;
}
