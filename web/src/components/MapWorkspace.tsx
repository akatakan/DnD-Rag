import { type FormEvent, useEffect, useRef, useState } from "react";
import { Eye, EyeOff, Grid3X3, Images, RefreshCw, Upload } from "lucide-react";
import { api } from "../api";
import type { CommandResponse, MapAsset, MapScene, MapToken } from "../types";
import MapBoard from "./MapBoard";

export default function MapWorkspace({
  initialScene,
  gameRevision,
  token,
  canControl,
  activeCombatantId,
  onError,
}: {
  initialScene: MapScene;
  gameRevision: number;
  token: string;
  canControl: boolean;
  activeCombatantId?: string;
  onError: (value: string) => void;
}) {
  const [scene, setScene] = useState(initialScene);
  const [assets, setAssets] = useState<MapAsset[]>([]);
  const [busy, setBusy] = useState("");
  const [fogPaintMode, setFogPaintMode] = useState<"reveal" | "hide" | null>(null);
  const dirtyRef = useRef(false);
  const busyRef = useRef(false);
  const mountedRef = useRef(true);
  const gameRevisionRef = useRef(gameRevision);
  const tokenRef = useRef(token);

  useEffect(() => {
    tokenRef.current = token;
    busyRef.current = false;
    gameRevisionRef.current = gameRevision;
    dirtyRef.current = false;
    setBusy("");
    setAssets([]);
    setFogPaintMode(null);
    setScene(initialScene);
  }, [token]);

  useEffect(() => {
    gameRevisionRef.current = Math.max(gameRevisionRef.current, gameRevision);
  }, [gameRevision]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (!dirtyRef.current && initialScene.revision >= scene.revision) {
      setScene(initialScene);
    }
  }, [initialScene, scene.revision]);

  useEffect(() => {
    if (!canControl) return;
    let disposed = false;
    api.mapAssets(token)
      .then((result) => {
        if (!disposed) setAssets(result.assets);
      })
      .catch((reason) => {
        if (!disposed) {
          onError(reason instanceof Error ? reason.message : "Haritalar alınamadı.");
        }
      });
    return () => { disposed = true; };
  }, [canControl, onError, token]);

  function change(patch: Partial<MapScene>) {
    dirtyRef.current = true;
    setScene((current) => ({ ...current, ...patch }));
  }

  async function upload(file: File | undefined) {
    if (!file || busyRef.current) return;
    if (!["image/png", "image/jpeg"].includes(file.type)) {
      onError("Yalnız PNG veya JPEG harita yüklenebilir.");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      onError("Harita en fazla 10 MiB olabilir.");
      return;
    }
    busyRef.current = true;
    setBusy("upload");
    const operationToken = token;
    try {
      const asset = await api.uploadMapAsset(operationToken, file);
      if (!mountedRef.current || tokenRef.current !== operationToken) return;
      setAssets((current) => [asset, ...current]);
      change({ asset_id: asset.id, asset });
      onError("");
    } catch (reason) {
      if (mountedRef.current && tokenRef.current === operationToken) {
        onError(reason instanceof Error ? reason.message : "Harita yüklenemedi.");
      }
    } finally {
      if (tokenRef.current === operationToken) {
        busyRef.current = false;
        if (mountedRef.current) setBusy("");
      }
    }
  }

  async function save(event: FormEvent) {
    event.preventDefault();
    if (busyRef.current) return;
    busyRef.current = true;
    setBusy("save");
    const operationToken = token;
    try {
      const response = await api.command<CommandResponse>(
        operationToken,
        "update_map_scene",
        {
          scene_revision: scene.revision,
          asset_id: scene.asset_id,
          name: scene.name,
          grid_type: scene.grid_type,
          grid_size_px: scene.grid_size_px,
          distance_per_cell: scene.distance_per_cell,
          distance_unit: scene.distance_unit,
          viewport: scene.viewport,
          published: scene.published,
        },
        gameRevisionRef.current,
      );
      if (!mountedRef.current || tokenRef.current !== operationToken) return;
      gameRevisionRef.current = Math.max(
        gameRevisionRef.current,
        response.revision,
      );
      const fresh = await api.mapScene(operationToken);
      if (!mountedRef.current || tokenRef.current !== operationToken) return;
      dirtyRef.current = false;
      setScene(fresh);
      onError("");
    } catch (reason) {
      if (mountedRef.current && tokenRef.current === operationToken) {
        onError(reason instanceof Error ? reason.message : "Map scene kaydedilemedi.");
      }
    } finally {
      if (tokenRef.current === operationToken) {
        busyRef.current = false;
        if (mountedRef.current) setBusy("");
      }
    }
  }

  async function mapCommand(
    type:
      | "sync_map_tokens"
      | "move_map_token"
      | "set_map_fog"
      | "paint_map_fog"
      | "map_ping"
      | "map_draw",
    payload: Record<string, unknown> = {},
  ) {
    if (busyRef.current || dirtyRef.current) return;
    busyRef.current = true;
    setBusy(type);
    const operationToken = token;
    try {
      const response = await api.command<CommandResponse>(
        operationToken,
        type,
        payload,
        gameRevisionRef.current,
      );
      if (!mountedRef.current || tokenRef.current !== operationToken) return;
      gameRevisionRef.current = Math.max(
        gameRevisionRef.current,
        response.revision,
      );
      const fresh = await api.mapScene(operationToken);
      if (!mountedRef.current || tokenRef.current !== operationToken) return;
      setScene(fresh);
      onError("");
    } catch (reason) {
      if (mountedRef.current && tokenRef.current === operationToken) {
        onError(reason instanceof Error ? reason.message : "Map islemi tamamlanamadi.");
        const fresh = await api.mapScene(operationToken).catch(() => null);
        if (
          fresh
          && mountedRef.current
          && tokenRef.current === operationToken
          && !dirtyRef.current
        ) setScene(fresh);
      }
    } finally {
      if (tokenRef.current === operationToken) {
        busyRef.current = false;
        if (mountedRef.current) setBusy("");
      }
    }
  }

  function moveToken(mapToken: MapToken, x: number, y: number) {
    void mapCommand("move_map_token", {
      token_id: mapToken.id,
      token_revision: mapToken.revision,
      x,
      y,
    });
  }

  return (
    <section className="map-workspace">
      <header>
        <div>
          <span className="eyebrow">Virtual Tabletop</span>
          <h2><Grid3X3 /> Map Scene</h2>
        </div>
        <div className="map-header-actions">
          <button
            type="button"
            disabled={
              !canControl
              || Boolean(busy)
              || dirtyRef.current
              || !scene.asset
            }
            onClick={() => void mapCommand("sync_map_tokens")}
          >
            <RefreshCw /> Tokenları senkronize et
          </button>
          <span className={`map-publish-state ${scene.published ? "published" : ""}`}>
            {scene.published ? <Eye /> : <EyeOff />}
            {scene.published ? "Oyunculara açık" : "DM taslağı"}
          </span>
        </div>
      </header>
      <MapBoard
        scene={scene}
        token={token}
        activeCombatantId={activeCombatantId}
        onMoveToken={
          canControl && !busy && !dirtyRef.current ? moveToken : undefined
        }
        fogPaintMode={
          canControl && !busy && !dirtyRef.current ? fogPaintMode : null
        }
        onPaintFog={(cells) => void mapCommand("paint_map_fog", {
          fog_revision: scene.fog.revision,
          mode: fogPaintMode,
          cells,
        })}
        onMapPing={(x, y) => void mapCommand("map_ping", { x, y })}
        onMapDraw={(points) => void mapCommand("map_draw", { points })}
        allowDraw={canControl}
      />
      <div className="map-fog-controls">
        <label className="check-label">
          <input
            type="checkbox"
            checked={scene.fog.enabled}
            disabled={!canControl || Boolean(busy) || dirtyRef.current}
            onChange={(event) => void mapCommand("set_map_fog", {
              fog_revision: scene.fog.revision,
              enabled: event.target.checked,
            })}
          />
          Fog of war
        </label>
        <div className="button-row" aria-label="Fog fırçası">
          <button
            type="button"
            className={fogPaintMode === "reveal" ? "active" : ""}
            disabled={!canControl || Boolean(busy) || !scene.fog.enabled}
            onClick={() => setFogPaintMode((current) => (
              current === "reveal" ? null : "reveal"
            ))}
          >
            Alan aç
          </button>
          <button
            type="button"
            className={fogPaintMode === "hide" ? "active" : ""}
            disabled={!canControl || Boolean(busy) || !scene.fog.enabled}
            onClick={() => setFogPaintMode((current) => (
              current === "hide" ? null : "hide"
            ))}
          >
            Alan kapat
          </button>
        </div>
        <span>{scene.fog.revealed_cells?.length ?? 0} açık hücre</span>
      </div>
      {canControl && (
        <section className="map-asset-library" aria-labelledby="map-library-title">
          <div>
            <h3 id="map-library-title"><Images /> Harita kütüphanesi</h3>
            <small>
              Bir harita seçip aşağıdaki “Map scene kaydet” düğmesiyle masaya geçir.
              Yayın açıksa oyuncular kayıttan sonra anlık olarak yeni haritayı görür.
            </small>
          </div>
          {assets.length > 0 ? (
            <div className="map-asset-list">
              {assets.map((asset) => (
                <button
                  type="button"
                  key={asset.id}
                  aria-pressed={scene.asset_id === asset.id}
                  className={scene.asset_id === asset.id ? "active" : ""}
                  disabled={Boolean(busy)}
                  onClick={() => change({
                    asset_id: asset.id,
                    asset,
                  })}
                >
                  <span>{asset.original_name}</span>
                  <small>{asset.width}×{asset.height}</small>
                </button>
              ))}
            </div>
          ) : (
            <p className="muted">Henüz kayıtlı harita yok. PNG veya JPEG yükle.</p>
          )}
        </section>
      )}
      <form className="map-controls" onSubmit={save}>
        <label>
          Harita
          <select
            disabled={!canControl || Boolean(busy)}
            value={scene.asset_id ?? ""}
            onChange={(event) => {
              const asset = assets.find((item) => item.id === event.target.value) ?? null;
              change({ asset_id: asset?.id ?? null, asset, published: asset ? scene.published : false });
            }}
          >
            <option value="">Harita seçilmedi</option>
            {assets.map((asset) => (
              <option key={asset.id} value={asset.id}>
                {asset.original_name} · {asset.width}×{asset.height}
              </option>
            ))}
          </select>
        </label>
        <label className="map-upload">
          <Upload /> {busy === "upload" ? "Yükleniyor…" : "PNG/JPEG yükle"}
          <input
            type="file"
            accept="image/png,image/jpeg"
            disabled={!canControl || Boolean(busy)}
            onChange={(event) => {
              void upload(event.target.files?.[0]);
              event.currentTarget.value = "";
            }}
          />
        </label>
        <label>
          Scene adı
          <input disabled={!canControl || Boolean(busy)} maxLength={120} value={scene.name} onChange={(event) => change({ name: event.target.value })} />
        </label>
        <label>
          Grid
          <select disabled={!canControl || Boolean(busy)} value={scene.grid_type} onChange={(event) => change({ grid_type: event.target.value as MapScene["grid_type"] })}>
            <option value="none">Yok</option>
            <option value="square">Kare</option>
            <option value="hex">Hex</option>
          </select>
        </label>
        <label>
          Hücre px
          <input type="number" min={10} max={512} disabled={!canControl || Boolean(busy) || scene.grid_type === "none"} value={scene.grid_size_px} onChange={(event) => change({ grid_size_px: Number(event.target.value) })} />
        </label>
        <label>
          Ölçek
          <span className="map-scale-fields">
            <input type="number" min={0.1} max={1000} step={0.1} disabled={!canControl || Boolean(busy) || scene.grid_type === "none"} value={scene.distance_per_cell} onChange={(event) => change({ distance_per_cell: Number(event.target.value) })} />
            <select disabled={!canControl || Boolean(busy) || scene.grid_type === "none"} value={scene.distance_unit} onChange={(event) => change({ distance_unit: event.target.value as "ft" | "m" })}><option value="ft">ft</option><option value="m">m</option></select>
          </span>
        </label>
        {(["x", "y", "zoom"] as const).map((key) => (
          <label key={key}>
            Viewport {key}
            <input
              type="number"
              min={key === "zoom" ? 0.1 : -100000}
              max={key === "zoom" ? 8 : 100000}
              step={key === "zoom" ? 0.1 : 1}
              disabled={!canControl || Boolean(busy)}
              value={scene.viewport[key]}
              onChange={(event) => change({ viewport: { ...scene.viewport, [key]: Number(event.target.value) } })}
            />
          </label>
        ))}
        <label className="check-label map-publish-check">
          <input type="checkbox" disabled={!canControl || Boolean(busy) || !scene.asset_id} checked={scene.published} onChange={(event) => change({ published: event.target.checked })} />
          Oyunculara yayınla
        </label>
        <button className="primary-button" disabled={!canControl || Boolean(busy) || !scene.name.trim()}>
          {busy === "save" ? "Kaydediliyor…" : "Map scene kaydet"}
        </button>
      </form>
    </section>
  );
}
