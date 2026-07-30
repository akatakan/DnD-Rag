import {
  type KeyboardEvent,
  type PointerEvent,
  useEffect,
  useId,
  useRef,
  useState,
} from "react";
import { ImageOff, Map as MapIcon, MapPin, MousePointer2, Pencil, Ruler } from "lucide-react";
import { api } from "../api";
import type { MapScene, MapToken } from "../types";

export default function MapBoard({
  scene,
  token,
  compact = false,
  activeCombatantId,
  onMoveToken,
  fogPaintMode,
  onPaintFog,
  onMapPing,
  onMapDraw,
  allowDraw = false,
}: {
  scene: MapScene;
  token: string;
  compact?: boolean;
  activeCombatantId?: string;
  onMoveToken?: (token: MapToken, x: number, y: number) => void;
  fogPaintMode?: "reveal" | "hide" | null;
  onPaintFog?: (cells: [number, number][]) => void;
  onMapPing?: (x: number, y: number) => void;
  onMapDraw?: (points: [number, number][]) => void;
  allowDraw?: boolean;
}) {
  const [imageUrl, setImageUrl] = useState("");
  const [failed, setFailed] = useState(false);
  const [fogUrl, setFogUrl] = useState("");
  const [preview, setPreview] = useState<Record<string, { x: number; y: number }>>({});
  const [tool, setTool] = useState<"move" | "ruler" | "ping" | "draw">("move");
  const [rulerLine, setRulerLine] = useState<{
    start: [number, number];
    end: [number, number];
  } | null>(null);
  const [clock, setClock] = useState(() => Date.now());
  const [fogKeyboardCell, setFogKeyboardCell] = useState<[number, number]>([0, 0]);
  const boardRef = useRef<HTMLElement | null>(null);
  const fogCellsRef = useRef(new Set<string>());
  const drawPointsRef = useRef<[number, number][]>([]);
  const patternId = `map-grid-${useId().replaceAll(":", "")}`;

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    let disposed = false;
    setImageUrl("");
    setFailed(false);
    if (!scene.asset?.url) return () => controller.abort();
    api.mapAssetBlob(token, scene.asset.url, controller.signal)
      .then((blob) => {
        if (disposed) return;
        objectUrl = URL.createObjectURL(blob);
        setImageUrl(objectUrl);
      })
      .catch((reason) => {
        if (
          !disposed
          && !(reason instanceof DOMException && reason.name === "AbortError")
        ) {
          setFailed(true);
        }
      });
    return () => {
      disposed = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [scene.asset?.url, token]);

  useEffect(() => {
    const controller = new AbortController();
    let objectUrl = "";
    let disposed = false;
    setFogUrl("");
    if (!scene.fog.mask_url) return () => controller.abort();
    api.mapAssetBlob(token, scene.fog.mask_url, controller.signal)
      .then((blob) => {
        if (disposed) return;
        objectUrl = URL.createObjectURL(blob);
        setFogUrl(objectUrl);
      })
      .catch(() => {
        // A stale fog revision can disappear while a newer snapshot arrives.
      });
    return () => {
      disposed = true;
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [scene.fog.mask_url, token]);

  useEffect(() => {
    const nextExpiry = scene.signals.reduce(
      (earliest, signal) => {
        const expiry = Date.parse(signal.expires_at);
        return expiry > clock ? Math.min(earliest, expiry) : earliest;
      },
      Number.POSITIVE_INFINITY,
    );
    if (!Number.isFinite(nextExpiry)) return;
    const timer = window.setTimeout(
      () => setClock(Date.now()),
      Math.max(0, nextExpiry - Date.now()) + 20,
    );
    return () => window.clearTimeout(timer);
  }, [clock, scene.signals]);

  useEffect(() => {
    setPreview({});
    setTool("move");
    setRulerLine(null);
    setFogKeyboardCell([0, 0]);
    fogCellsRef.current.clear();
    drawPointsRef.current = [];
  }, [scene.asset?.id, token]);

  if (!scene.asset) {
    return (
      <div className={`map-board empty ${compact ? "compact" : ""}`}>
        <MapIcon />
        <strong>{scene.published ? "Harita bulunamadı" : "Harita yayınlanmadı"}</strong>
      </div>
    );
  }
  if (failed) {
    return (
      <div className={`map-board empty ${compact ? "compact" : ""}`}>
        <ImageOff />
        <strong>Harita görseli alınamadı</strong>
      </div>
    );
  }

  const width = scene.asset.width;
  const height = scene.asset.height;
  const size = scene.grid_size_px;
  const fogWidth = Math.ceil(width / size) * size;
  const fogHeight = Math.ceil(height / size) * size;
  const playerFog = scene.fog.revealed_cells === null;
  const hexWidth = size * Math.sqrt(3);
  const gridPath = scene.grid_type === "hex"
    ? `M${hexWidth / 2},0 L${hexWidth},${size / 2} L${hexWidth},${size * 1.5} L${hexWidth / 2},${size * 2} L0,${size * 1.5} L0,${size / 2} Z`
    : `M${size},0 H0 V${size}`;

  function mapPoint(
    clientX: number,
    clientY: number,
    mapToken: MapToken,
  ) {
    const bounds = boardRef.current?.getBoundingClientRect();
    if (!bounds) return null;
    let x = (clientX - bounds.left - scene.viewport.x) / scene.viewport.zoom;
    let y = (clientY - bounds.top - scene.viewport.y) / scene.viewport.zoom;
    if (scene.grid_type !== "none") {
      x = Math.floor(x / size) * size + size / 2;
      y = Math.floor(y / size) * size + size / 2;
    }
    return clampPoint(x, y, mapToken);
  }

  function clampPoint(x: number, y: number, mapToken: MapToken) {
    const half = mapToken.size_px / 2;
    const minimumX = Math.min(half, width / 2);
    const maximumX = Math.max(minimumX, width - half);
    const minimumY = Math.min(half, height / 2);
    const maximumY = Math.max(minimumY, height - half);
    return {
      x: Math.max(minimumX, Math.min(maximumX, x)),
      y: Math.max(minimumY, Math.min(maximumY, y)),
    };
  }

  function moveFromKeyboard(
    event: KeyboardEvent<HTMLButtonElement>,
    mapToken: MapToken,
  ) {
    if (!onMoveToken || !mapToken.can_move) return;
    const step = (scene.grid_type === "none" ? 10 : size)
      * (event.shiftKey ? 5 : 1);
    const deltas: Record<string, [number, number]> = {
      ArrowLeft: [-step, 0],
      ArrowRight: [step, 0],
      ArrowUp: [0, -step],
      ArrowDown: [0, step],
    };
    const delta = deltas[event.key];
    if (!delta) return;
    event.preventDefault();
    const point = clampPoint(
      mapToken.x + delta[0],
      mapToken.y + delta[1],
      mapToken,
    );
    onMoveToken(mapToken, point.x, point.y);
  }

  function fogCell(clientX: number, clientY: number): [number, number] | null {
    const bounds = boardRef.current?.getBoundingClientRect();
    if (!bounds) return null;
    const x = (clientX - bounds.left - scene.viewport.x) / scene.viewport.zoom;
    const y = (clientY - bounds.top - scene.viewport.y) / scene.viewport.zoom;
    if (x < 0 || y < 0 || x >= width || y >= height) return null;
    return [Math.floor(x / size), Math.floor(y / size)];
  }

  function collectFogCell(clientX: number, clientY: number) {
    const cell = fogCell(clientX, clientY);
    if (cell && fogCellsRef.current.size < 512) {
      fogCellsRef.current.add(`${cell[0]}:${cell[1]}`);
    }
  }

  function toolPoint(clientX: number, clientY: number): [number, number] | null {
    const bounds = boardRef.current?.getBoundingClientRect();
    if (!bounds) return null;
    const x = (clientX - bounds.left - scene.viewport.x) / scene.viewport.zoom;
    const y = (clientY - bounds.top - scene.viewport.y) / scene.viewport.zoom;
    if (x < 0 || y < 0 || x > width || y > height) return null;
    return [x, y];
  }

  const visibleSignals = scene.signals.filter(
    (signal) => Date.parse(signal.expires_at) > clock,
  );

  return (
    <figure
      ref={boardRef}
      className={`map-board ${compact ? "compact" : ""}`}
    >
      <div
        className="map-transform"
        style={{
          width,
          height,
          transform: `translate(${scene.viewport.x}px, ${scene.viewport.y}px) scale(${scene.viewport.zoom})`,
        }}
      >
        {imageUrl
          ? <img src={imageUrl} width={width} height={height} alt={scene.name} draggable={false} />
          : <div className="map-loading">Harita hazırlanıyor…</div>}
        {scene.grid_type !== "none" && (
          <svg className="map-grid-overlay" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
            <defs>
              <pattern
                id={patternId}
                width={scene.grid_type === "hex" ? hexWidth : size}
                height={scene.grid_type === "hex" ? size * 2 : size}
                patternUnits="userSpaceOnUse"
              >
                <path d={gridPath} fill="none" />
              </pattern>
            </defs>
            <rect width="100%" height="100%" fill={`url(#${patternId})`} />
          </svg>
        )}
        {scene.fog.enabled && fogUrl && (
          <img
            className={`map-fog-mask ${playerFog ? "player" : "dm"}`}
            src={fogUrl}
            width={fogWidth}
            height={fogHeight}
            style={{ width: fogWidth, height: fogHeight }}
            alt=""
            aria-hidden="true"
            draggable={false}
          />
        )}
        <svg
          className="map-signal-layer"
          viewBox={`0 0 ${width} ${height}`}
          aria-label="Geçici harita çizimleri"
        >
          {visibleSignals
            .filter((signal) => signal.kind === "draw")
            .map((signal) => (
              <polyline
                key={signal.id}
                points={(signal.payload.points ?? [])
                  .map((point) => point.join(","))
                  .join(" ")}
              />
            ))}
          {rulerLine && (
            <line
              className="map-ruler-line"
              x1={rulerLine.start[0]}
              y1={rulerLine.start[1]}
              x2={rulerLine.end[0]}
              y2={rulerLine.end[1]}
            />
          )}
        </svg>
        {visibleSignals
          .filter((signal) => signal.kind === "ping")
          .map((signal) => (
            <span
              key={signal.id}
              className="map-ping"
              style={{
                left: signal.payload.x,
                top: signal.payload.y,
              }}
              title={`${signal.actor_name} ping`}
            />
          ))}
        {rulerLine && (
          <span
            className="map-ruler-label"
            style={{ left: rulerLine.end[0], top: rulerLine.end[1] }}
          >
            {(
              Math.hypot(
                rulerLine.end[0] - rulerLine.start[0],
                rulerLine.end[1] - rulerLine.start[1],
              ) / size * scene.distance_per_cell
            ).toFixed(1)} {scene.distance_unit}
          </span>
        )}
        <div className="map-token-layer" aria-label="Encounter tokenlari">
          {scene.tokens.map((mapToken) => {
            const position = preview[mapToken.id] ?? mapToken;
            const active = mapToken.combatant_id === activeCombatantId;
            return (
              <button
                key={mapToken.id}
                type="button"
                className={`map-token ${active ? "active" : ""} ${mapToken.can_move ? "movable" : ""}`}
                style={{
                  width: mapToken.size_px,
                  height: mapToken.size_px,
                  left: position.x,
                  top: position.y,
                }}
                aria-label={`${mapToken.name} token${mapToken.can_move ? ", taşınabilir" : ""}`}
                aria-disabled={!mapToken.can_move}
                aria-current={active ? "true" : undefined}
                title={`${mapToken.name} · init ${mapToken.initiative}${typeof mapToken.hp === "number" ? ` · ${mapToken.hp}/${mapToken.max_hp ?? "?"} HP` : ""}`}
                onKeyDown={(event) => moveFromKeyboard(event, mapToken)}
                onPointerDown={(event: PointerEvent<HTMLButtonElement>) => {
                  if (
                    event.button !== 0
                    || !onMoveToken
                    || !mapToken.can_move
                  ) return;
                  event.currentTarget.setPointerCapture(event.pointerId);
                }}
                onPointerMove={(event: PointerEvent<HTMLButtonElement>) => {
                  if (!event.currentTarget.hasPointerCapture(event.pointerId)) {
                    return;
                  }
                  const point = mapPoint(
                    event.clientX,
                    event.clientY,
                    mapToken,
                  );
                  if (point) {
                    setPreview((current) => ({
                      ...current,
                      [mapToken.id]: point,
                    }));
                  }
                }}
                onPointerUp={(event: PointerEvent<HTMLButtonElement>) => {
                  if (!event.currentTarget.hasPointerCapture(event.pointerId)) {
                    return;
                  }
                  event.currentTarget.releasePointerCapture(event.pointerId);
                  const point = mapPoint(
                    event.clientX,
                    event.clientY,
                    mapToken,
                  );
                  setPreview((current) => {
                    const next = { ...current };
                    delete next[mapToken.id];
                    return next;
                  });
                  if (
                    onMoveToken
                    && mapToken.can_move
                    && point
                    && (point.x !== mapToken.x || point.y !== mapToken.y)
                  ) {
                    onMoveToken(mapToken, point.x, point.y);
                  }
                }}
                onPointerCancel={() => {
                  setPreview((current) => {
                    const next = { ...current };
                    delete next[mapToken.id];
                    return next;
                  });
                }}
              >
                <span>{mapToken.name.slice(0, 2).toUpperCase()}</span>
              </button>
            );
          })}
        </div>
        {fogPaintMode && onPaintFog && (
          <div
            className="map-fog-editor"
            role="application"
            tabIndex={0}
            aria-label={`Fog ${fogPaintMode === "reveal" ? "açma" : "kapatma"} fırçası. Ok tuşlarıyla hücre seçin, Enter veya boşluk ile uygulayın.`}
            onKeyDown={(event) => {
              const columns = Math.ceil(width / size);
              const rows = Math.ceil(height / size);
              const deltas: Record<string, [number, number]> = {
                ArrowLeft: [-1, 0],
                ArrowRight: [1, 0],
                ArrowUp: [0, -1],
                ArrowDown: [0, 1],
              };
              const delta = deltas[event.key];
              if (delta) {
                event.preventDefault();
                setFogKeyboardCell(([x, y]) => [
                  Math.max(0, Math.min(columns - 1, x + delta[0])),
                  Math.max(0, Math.min(rows - 1, y + delta[1])),
                ]);
              } else if (event.key === "Enter" || event.key === " ") {
                event.preventDefault();
                onPaintFog([fogKeyboardCell]);
              }
            }}
            onPointerDown={(event) => {
              if (event.pointerType === "mouse" && event.button !== 0) return;
              fogCellsRef.current.clear();
              event.currentTarget.setPointerCapture(event.pointerId);
              collectFogCell(event.clientX, event.clientY);
            }}
            onPointerMove={(event) => {
              if (event.currentTarget.hasPointerCapture(event.pointerId)) {
                collectFogCell(event.clientX, event.clientY);
              }
            }}
            onPointerUp={(event) => {
              if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
              event.currentTarget.releasePointerCapture(event.pointerId);
              collectFogCell(event.clientX, event.clientY);
              const cells = [...fogCellsRef.current].map((entry) => (
                entry.split(":").map(Number) as [number, number]
              ));
              fogCellsRef.current.clear();
              if (cells.length) onPaintFog(cells);
            }}
            onPointerCancel={() => fogCellsRef.current.clear()}
          >
            <span
              className="map-fog-keyboard-cell"
              style={{
                left: fogKeyboardCell[0] * size,
                top: fogKeyboardCell[1] * size,
                width: Math.min(size, width - fogKeyboardCell[0] * size),
                height: Math.min(size, height - fogKeyboardCell[1] * size),
              }}
              aria-hidden="true"
            />
          </div>
        )}
        {!fogPaintMode && tool !== "move" && (
          <div
            className="map-tool-layer"
            role="application"
            aria-label={`${tool} harita aracı`}
            onPointerDown={(event) => {
              if (event.pointerType === "mouse" && event.button !== 0) return;
              const point = toolPoint(event.clientX, event.clientY);
              if (!point) return;
              if (tool === "ping") {
                onMapPing?.(point[0], point[1]);
                return;
              }
              event.currentTarget.setPointerCapture(event.pointerId);
              if (tool === "ruler") {
                setRulerLine({ start: point, end: point });
              } else {
                drawPointsRef.current = [point];
              }
            }}
            onPointerMove={(event) => {
              if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
              const point = toolPoint(event.clientX, event.clientY);
              if (!point) return;
              if (tool === "ruler") {
                setRulerLine((current) => (
                  current ? { ...current, end: point } : null
                ));
              } else {
                const points = drawPointsRef.current;
                const previous = points.at(-1);
                if (
                  points.length < 64
                  && (!previous || Math.hypot(
                    point[0] - previous[0],
                    point[1] - previous[1],
                  ) >= 4)
                ) points.push(point);
              }
            }}
            onPointerUp={(event) => {
              if (!event.currentTarget.hasPointerCapture(event.pointerId)) return;
              event.currentTarget.releasePointerCapture(event.pointerId);
              if (tool === "draw" && drawPointsRef.current.length >= 2) {
                onMapDraw?.([...drawPointsRef.current]);
              }
              drawPointsRef.current = [];
            }}
            onPointerCancel={() => {
              drawPointsRef.current = [];
              if (tool === "ruler") setRulerLine(null);
            }}
          />
        )}
      </div>
      <div className="map-toolbox" role="toolbar" aria-label="Harita araçları">
        <button type="button" className={tool === "move" ? "active" : ""} onClick={() => { setTool("move"); setRulerLine(null); }} aria-label="Token aracı" aria-pressed={tool === "move"}><MousePointer2 /></button>
        <button type="button" className={tool === "ruler" ? "active" : ""} onClick={() => setTool("ruler")} aria-label="Mesafe ölç" aria-pressed={tool === "ruler"}><Ruler /></button>
        {onMapPing && <button type="button" className={tool === "ping" ? "active" : ""} onClick={() => setTool("ping")} aria-label="Haritaya ping at" aria-pressed={tool === "ping"}><MapPin /></button>}
        {allowDraw && onMapDraw && <button type="button" className={tool === "draw" ? "active" : ""} onClick={() => setTool("draw")} aria-label="Geçici çizim yap" aria-pressed={tool === "draw"}><Pencil /></button>}
      </div>
      <figcaption>
        <strong>{scene.name}</strong>
        <span>
          {scene.grid_type === "none"
            ? "Grid yok"
            : `${scene.grid_type} · ${scene.distance_per_cell} ${scene.distance_unit}/kare`}
          {" · "}{Math.round(scene.viewport.zoom * 100)}%
        </span>
      </figcaption>
    </figure>
  );
}
