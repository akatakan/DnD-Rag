import { useEffect, useRef } from "react";
import * as CANNON from "cannon-es";
import {
  BoxGeometry,
  CanvasTexture,
  DirectionalLight,
  DodecahedronGeometry,
  DoubleSide,
  HemisphereLight,
  IcosahedronGeometry,
  type Material,
  Mesh,
  MeshBasicMaterial,
  MeshStandardMaterial,
  OctahedronGeometry,
  PCFSoftShadowMap,
  PerspectiveCamera,
  PlaneGeometry,
  PointLight,
  PolyhedronGeometry,
  Scene,
  SphereGeometry,
  SRGBColorSpace,
  TetrahedronGeometry,
  WebGLRenderer,
} from "three";
import { playDiceImpact } from "../diceAudio";
import type { DiceRollPayload, DiceSides, DiceTheme } from "../types";

const THEME: Record<DiceTheme, {
  kept: number;
  discarded: number;
  edge: number;
  tray: number;
  glow: number;
}> = {
  crimson: {
    kept: 0xa92f28,
    discarded: 0x3b403d,
    edge: 0xffd9d5,
    tray: 0x1d2420,
    glow: 0xe35b51,
  },
  arcane: {
    kept: 0x5b45a7,
    discarded: 0x27253a,
    edge: 0xded5ff,
    tray: 0x171625,
    glow: 0x9d87ff,
  },
  ivory: {
    kept: 0xe8ddc8,
    discarded: 0x827a6d,
    edge: 0x4b342b,
    tray: 0x302a24,
    glow: 0xf0c987,
  },
};

function visualRandom(seed: number) {
  // Presentation-only deterministic variation. The labels are still populated from
  // the authoritative server result and physics never decides a rolled value.
  let state = seed >>> 0 || 0x6d2b79f5;
  return () => {
    state += 0x6d2b79f5;
    let value = state;
    value = Math.imul(value ^ (value >>> 15), value | 1);
    value ^= value + Math.imul(value ^ (value >>> 7), value | 61);
    return ((value ^ (value >>> 14)) >>> 0) / 4294967296;
  };
}

function d10Geometry() {
  const vertices: number[] = [0, 0.78, 0, 0, -0.78, 0];
  for (let index = 0; index < 10; index += 1) {
    const angle = (index / 10) * Math.PI * 2;
    const y = index % 2 === 0 ? 0.18 : -0.18;
    vertices.push(Math.cos(angle) * 0.54, y, Math.sin(angle) * 0.54);
  }
  const indices: number[] = [];
  for (let index = 0; index < 10; index += 1) {
    const current = 2 + index;
    const next = 2 + ((index + 1) % 10);
    if (index % 2 === 0) {
      indices.push(0, current, next, 1, next, current);
    } else {
      indices.push(0, next, current, 1, current, next);
    }
  }
  return new PolyhedronGeometry(vertices, indices, 0.62, 0);
}

function geometryFor(sides: DiceSides) {
  switch (sides) {
    case 4:
      return new TetrahedronGeometry(0.61);
    case 6:
      return new BoxGeometry(0.88, 0.88, 0.88);
    case 8:
      return new OctahedronGeometry(0.62);
    case 10:
      return d10Geometry();
    case 12:
      return new DodecahedronGeometry(0.58);
    case 20:
      return new IcosahedronGeometry(0.59);
    case 100:
      // 10 longitudinal segments and 6 rings produce exactly 100 triangular
      // faces while retaining the near-spherical zocchihedron profile.
      return new SphereGeometry(0.58, 10, 6);
  }
}

function keptIndexes(result: DiceRollPayload) {
  if (result.kept.length === result.rolls.length) {
    return new Set(result.rolls.map((_, index) => index));
  }
  const indexes = new Set<number>();
  const remaining = [...result.kept];
  result.rolls.forEach((value, index) => {
    const match = remaining.indexOf(value);
    if (match >= 0) {
      indexes.add(index);
      remaining.splice(match, 1);
    }
  });
  return indexes;
}

function shownResultFaces(result: DiceRollPayload) {
  return result.rolls.slice(0, 12).join(",");
}

function faceLabel(
  value: number,
  color: string,
  position: [number, number, number],
  rotation: [number, number, number],
  size: number,
  visible = true,
) {
  const canvas = document.createElement("canvas");
  canvas.width = 256;
  canvas.height = 256;
  const context = canvas.getContext("2d");
  if (!context) throw new Error("Zar yuzeyi cizilemedi.");
  context.clearRect(0, 0, 256, 256);
  context.font = `900 ${value >= 100 ? 112 : value >= 10 ? 138 : 164}px Georgia`;
  context.textAlign = "center";
  context.textBaseline = "middle";
  context.lineJoin = "round";
  context.lineWidth = 12;
  context.strokeStyle = "rgba(255, 255, 255, .28)";
  context.strokeText(String(value), 128, 134);
  context.fillStyle = color;
  context.fillText(String(value), 128, 134);
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  texture.needsUpdate = true;
  const material = new MeshBasicMaterial({
    map: texture,
    transparent: true,
    alphaTest: 0.08,
    side: DoubleSide,
    depthTest: true,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -2,
  });
  const geometry = new PlaneGeometry(size, size);
  const mesh = new Mesh(geometry, material);
  mesh.position.set(...position);
  mesh.rotation.set(...rotation);
  mesh.visible = visible;
  mesh.renderOrder = 2;
  return { mesh, material, texture, geometry };
}

function labelsForDie(value: number, sides: DiceSides, color: string) {
  const radius = sides === 6 ? 0.446 : 0.57;
  const size = sides === 6 ? 0.42 : 0.34;
  const nextValue = (offset: number) => ((value - 1 + offset) % sides) + 1;
  const top = faceLabel(
    value,
    color,
    [0, radius + 0.004, 0],
    [-Math.PI / 2, 0, 0],
    size,
    false,
  );
  const sideLabels = sides === 6
    ? [
        faceLabel(
          nextValue(1),
          color,
          [0, 0, radius + 0.004],
          [0, 0, 0],
          size,
        ),
        faceLabel(
          nextValue(2),
          color,
          [radius + 0.004, 0, 0],
          [0, Math.PI / 2, 0],
          size,
        ),
      ]
    : [];
  return { top, all: [top, ...sideLabels] };
}

export default function Dice3DTray({
  result,
  sides,
  theme,
  sound,
  tossKey,
}: {
  result: DiceRollPayload;
  sides: DiceSides;
  theme: DiceTheme;
  sound: boolean;
  tossKey: number;
}) {
  const hostRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const host = hostRef.current;
    if (!host) return;
    delete host.dataset.reducedMotion;
    delete host.dataset.rendererUnavailable;
    const reducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (reducedMotion) {
      host.dataset.reducedMotion = "true";
      return;
    }

    const width = Math.max(320, host.clientWidth);
    const height = Math.max(220, host.clientHeight);
    const narrowStage = width / height < 0.72;
    const trayHalfWidth = narrowStage ? 2.55 : 5.2;
    const trayHalfDepth = 3.2;
    const colors = THEME[theme];
    let renderer: WebGLRenderer | null = null;
    try {
      renderer = new WebGLRenderer({
        antialias: true,
        alpha: true,
        powerPreference: "high-performance",
      });
      renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
      renderer.setSize(width, height, false);
      if (renderer.getContext().isContextLost()) {
        renderer.dispose();
        renderer.forceContextLoss();
        host.dataset.rendererUnavailable = "true";
        return;
      }
    } catch {
      renderer?.dispose();
      renderer?.forceContextLoss();
      host.dataset.rendererUnavailable = "true";
      return;
    }
    if (!renderer) {
      host.dataset.rendererUnavailable = "true";
      return;
    }
    host.dataset.renderer = "webgl";
    host.dataset.animationState = "running";
    host.dataset.resultFaces = shownResultFaces(result);
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = PCFSoftShadowMap;
    renderer.outputColorSpace = SRGBColorSpace;
    renderer.domElement.setAttribute("aria-hidden", "true");
    host.appendChild(renderer.domElement);
    let disposed = false;
    const handleContextLost = (event: Event) => {
      event.preventDefault();
      if (!disposed) host.dataset.rendererUnavailable = "true";
    };
    renderer.domElement.addEventListener(
      "webglcontextlost", handleContextLost,
    );

    const scene = new Scene();
    const camera = new PerspectiveCamera(
      narrowStage ? 44 : 38,
      width / height,
      0.1,
      100,
    );
    camera.position.set(
      0,
      narrowStage ? 4.6 : 4.2,
      narrowStage ? 12.8 : 11.2,
    );
    camera.lookAt(0, 0.65, 0.8);

    scene.add(new HemisphereLight(0xffffff, colors.tray, 1.35));
    const keyLight = new DirectionalLight(0xffffff, 2.3);
    keyLight.position.set(-4, 8, 4);
    keyLight.castShadow = true;
    scene.add(keyLight);
    const rimLight = new PointLight(colors.glow, 18, 16);
    rimLight.position.set(4, 3, -3);
    scene.add(rimLight);

    const world = new CANNON.World({
      gravity: new CANNON.Vec3(0, -18, 0),
    });
    world.allowSleep = true;
    world.defaultContactMaterial.friction = 0.28;
    world.defaultContactMaterial.restitution = 0.48;
    const ground = new CANNON.Body({
      mass: 0,
      shape: new CANNON.Box(
        new CANNON.Vec3(trayHalfWidth, 0.18, trayHalfDepth),
      ),
      position: new CANNON.Vec3(0, -1.35, 0.8),
    });
    world.addBody(ground);
    const staticBodies = [ground];
    [
      [0, -0.65, -trayHalfDepth + 0.8, trayHalfWidth + 0.1, 0.8, 0.12],
      [0, -0.65, trayHalfDepth + 0.8, trayHalfWidth + 0.1, 0.8, 0.12],
      [-trayHalfWidth, -0.65, 0.8, 0.12, 0.8, trayHalfDepth - 0.1],
      [trayHalfWidth, -0.65, 0.8, 0.12, 0.8, trayHalfDepth - 0.1],
    ].forEach(([x, y, z, hx, hy, hz]) => {
      const body = new CANNON.Body({
        mass: 0,
        shape: new CANNON.Box(new CANNON.Vec3(hx, hy, hz)),
        position: new CANNON.Vec3(x, y, z),
      });
      world.addBody(body);
      staticBodies.push(body);
    });

    const kept = keptIndexes(result);
    const random = visualRandom(
      result.rolls.reduce(
        (seed, value, index) => Math.imul(seed ^ (value + index), 16777619),
        (tossKey + sides) | 0,
      ),
    );
    const objects: Array<{
      body: CANNON.Body;
      mesh: Mesh;
      labels: ReturnType<typeof labelsForDie>;
      settled: boolean;
      impact?: (event: { contact: CANNON.ContactEquation }) => void;
    }> = [];
    const shownRolls = result.rolls.slice(0, 12);
    shownRolls.forEach((value, index) => {
      const isKept = kept.has(index);
      const geometry = geometryFor(sides);
      geometry.computeVertexNormals();
      const material = new MeshStandardMaterial({
        color: isKept ? colors.kept : colors.discarded,
        roughness: 0.38,
        metalness: 0.22,
        flatShading: true,
        emissive: isKept ? colors.glow : 0x000000,
        emissiveIntensity: isKept ? 0.12 : 0,
      });
      const mesh = new Mesh(geometry, material);
      mesh.castShadow = true;
      mesh.receiveShadow = true;
      const labels = labelsForDie(
        value,
        sides,
        `#${colors.edge.toString(16).padStart(6, "0")}`,
      );
      labels.all.forEach((label) => mesh.add(label.mesh));
      scene.add(mesh);

      const physicsShape = sides === 6
        ? new CANNON.Box(new CANNON.Vec3(0.44, 0.44, 0.44))
        : new CANNON.Sphere(0.5);
      const body = new CANNON.Body({
        mass: 1,
        shape: physicsShape,
        linearDamping: 0.13,
        angularDamping: 0.16,
        sleepSpeedLimit: 0.15,
        sleepTimeLimit: 0.5,
      });
      const columns = narrowStage ? 3 : 4;
      const column = index % columns;
      const row = Math.floor(index / columns);
      const horizontalStep = narrowStage ? 1.55 : 1.85;
      const left = -((columns - 1) * horizontalStep) / 2;
      const spawnX = left + column * horizontalStep
        + (random() - 0.5) * 0.25;
      body.position.set(
        spawnX,
        4.3 + row * 1.05 + index * 0.08,
        0.9 + (random() - 0.5) * 1.8,
      );
      body.velocity.set(
        narrowStage
          ? -spawnX * 1.15 + (random() - 0.5) * 1.2
          : 4.2 + random() * 2.2,
        1.5 + random() * 2.2,
        (random() - 0.5) * 5.5,
      );
      body.angularVelocity.set(
        7 + random() * 7,
        8 + random() * 8,
        6 + random() * 9,
      );
      let impact: ((event: {
        contact: CANNON.ContactEquation;
      }) => void) | undefined;
      if (sound) {
        impact = (event: {
          contact: CANNON.ContactEquation;
        }) => {
          const velocity = Math.abs(
            event.contact.getImpactVelocityAlongNormal(),
          );
          if (velocity > 1.2) playDiceImpact(velocity);
        };
        body.addEventListener("collide", impact);
      }
      world.addBody(body);
      objects.push({ body, mesh, labels, settled: false, impact });
    });

    let frame = 0;
    let lastTime = performance.now();
    const startedAt = lastTime;
    const animate = (time: number) => {
      if (disposed) return;
      const delta = Math.min(1 / 20, (time - lastTime) / 1000);
      lastTime = time;
      world.step(1 / 60, delta, 4);
      objects.forEach((item) => {
        const { body, mesh, labels } = item;
        if (time - startedAt > 1_150 && !item.settled) {
          body.velocity.set(0, 0, 0);
          body.angularVelocity.set(0, 0, 0);
          body.quaternion.set(0, 0, 0, 1);
          body.sleep();
          item.settled = true;
          labels.top.mesh.visible = true;
        }
        mesh.position.copy(body.position);
        mesh.quaternion.copy(body.quaternion);
      });
      renderer.render(scene, camera);
      if (time - startedAt < 2750) {
        frame = requestAnimationFrame(animate);
      } else {
        host.dataset.animationState = "settled";
      }
    };
    frame = requestAnimationFrame(animate);

    let released = false;
    const release = () => {
      if (released) return;
      released = true;
      disposed = true;
      host.dataset.animationState = "released";
      cancelAnimationFrame(frame);
      objects.forEach(({ body, mesh, labels, impact }) => {
        if (impact) body.removeEventListener("collide", impact);
        world.removeBody(body);
        mesh.geometry.dispose();
        (mesh.material as Material).dispose();
        labels.all.forEach((label) => {
          label.geometry.dispose();
          label.material.dispose();
          label.texture.dispose();
        });
      });
      staticBodies.forEach((body) => world.removeBody(body));
      scene.clear();
      renderer.domElement.removeEventListener(
        "webglcontextlost", handleContextLost,
      );
      renderer.renderLists.dispose();
      renderer.dispose();
      renderer.forceContextLoss();
      renderer.domElement.remove();
    };
    const releaseTimer = window.setTimeout(release, 3300);
    return () => {
      window.clearTimeout(releaseTimer);
      release();
    };
  }, [result, sides, sound, theme, tossKey]);

  return (
    <div
      ref={hostRef}
      className={`dice-3d-tray theme-${theme}`}
      aria-hidden="true"
      data-testid="dice-3d-tray"
    >
      <div className="dice-3d-fallback">
        {result.rolls.slice(0, 12).map((value, index) => (
          <span
            className={keptIndexes(result).has(index) ? "kept" : "discarded"}
            key={`${tossKey}-${index}`}
          >
            {value}
          </span>
        ))}
      </div>
      <span className="dice-3d-overflow">
        {result.rolls.length > 12 ? `+${result.rolls.length - 12}` : ""}
      </span>
    </div>
  );
}
