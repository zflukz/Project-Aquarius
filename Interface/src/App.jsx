import { useMemo, useState } from "react";
import { APIProvider, Map, useMapsLibrary } from "@vis.gl/react-google-maps";
import AreaPolygon from "./AreaPolygon.jsx";

const runtimeKey = window.GOOGLE_MAPS_API_KEY;
const API_KEY = runtimeKey && !runtimeKey.startsWith("__") ? runtimeKey : import.meta.env.VITE_GOOGLE_MAPS_API_KEY;
const KMUTT = { lat: 13.6513, lng: 100.4964 };
const r7 = (n) => +n.toFixed(7);

export default function App() {
  if (!API_KEY) return <p className="missing">Set GOOGLE_MAPS_API_KEY (Docker) or VITE_GOOGLE_MAPS_API_KEY (npm run dev).</p>;
  return (
    <APIProvider apiKey={API_KEY}>
      <AreaSelect />
    </APIProvider>
  );
}

function AreaSelect() {
  const [points, setPoints] = useState([]);
  const [cursor, setCursor] = useState(null);
  const geometry = useMapsLibrary("geometry");

  const output = useMemo(() => {
    const pts = points.map((p) => ({ lat: r7(p.lat), lng: r7(p.lng) }));
    const data = { points: pts };
    if (pts.length) {
      data.center = {
        lat: r7(pts.reduce((s, p) => s + p.lat, 0) / pts.length),
        lng: r7(pts.reduce((s, p) => s + p.lng, 0) / pts.length),
      };
    }
    if (pts.length > 2 && geometry) data.area_m2 = Math.round(geometry.spherical.computeArea(points));
    return JSON.stringify(data, null, 2);
  }, [points, geometry]);

  return (
    <div className="app">
      <Map
        className="map"
        defaultCenter={KMUTT}
        defaultZoom={17}
        mapTypeId="hybrid"
        tilt={0}
        streetViewControl={false}
        clickableIcons={false}
        gestureHandling="greedy"
        onClick={(e) => e.detail.latLng && setPoints((p) => [...p, e.detail.latLng])}
        onMousemove={(e) => setCursor(e.detail.latLng)}
      >
        <AreaPolygon points={points} onChange={setPoints} />
      </Map>

      <aside>
        <h1>Area Select</h1>
        <p>Click the map to add points. Drag points to move, right-click a point to delete.</p>
        <div className="row">
          <button onClick={() => setPoints((p) => p.slice(0, -1))}>Undo</button>
          <button onClick={() => setPoints([])}>Clear</button>
          <button onClick={() => navigator.clipboard.writeText(output)}>Copy</button>
        </div>
        <pre>{output}</pre>
        <div className="cursor">{cursor ? `${r7(cursor.lat)}, ${r7(cursor.lng)}` : " "}</div>
      </aside>
    </div>
  );
}
