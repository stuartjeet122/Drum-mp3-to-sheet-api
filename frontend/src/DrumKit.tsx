import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { RotateCcw } from 'lucide-react';
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { RoomEnvironment } from 'three/addons/environments/RoomEnvironment.js';
import type { Drum } from './music';

export interface DrumKitHandle { hit: (drum: Drum, velocity?: number) => void }
interface AnimatedDrum { group: THREE.Group; head: THREE.MeshStandardMaterial; energy: number; rotation: number; cymbal: boolean }

export const DrumKit = forwardRef<DrumKitHandle, { onHit: (drum: Drum) => void }>(function DrumKit({ onHit }, ref) {
  const mount = useRef<HTMLDivElement>(null);
  const instruments = useRef<Partial<Record<Drum, AnimatedDrum>>>({});
  const handler = useRef(onHit);
  const reset = useRef<() => void>(() => {});
  const [unavailable, setUnavailable] = useState(false);
  useEffect(() => { handler.current = onHit; }, [onHit]);
  useImperativeHandle(ref, () => ({ hit(drum, velocity = 1) {
    const instrument = instruments.current[drum];
    if (instrument) instrument.energy = Math.max(instrument.energy, velocity);
  } }), []);

  useEffect(() => {
    const element = mount.current!;
    let renderer: THREE.WebGLRenderer;
    try { renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' }); }
    catch { setUnavailable(true); return; }
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.7));
    renderer.shadowMap.enabled = true;
    renderer.shadowMap.type = THREE.PCFSoftShadowMap;
    renderer.toneMapping = THREE.ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.4;
    renderer.domElement.setAttribute('aria-label', 'Interactive three-dimensional drum kit');
    renderer.domElement.setAttribute('role', 'img');
    element.appendChild(renderer.domElement);
    const scene = new THREE.Scene();
    const environmentGenerator = new THREE.PMREMGenerator(renderer);
    const room = new RoomEnvironment();
    const environment = environmentGenerator.fromScene(room, 0.04);
    scene.environment = environment.texture;
    room.dispose(); environmentGenerator.dispose();
    const camera = new THREE.PerspectiveCamera(35, 1, 0.1, 80);
    camera.position.set(3.6, 4.8, 6.6);
    const controls = new OrbitControls(camera, renderer.domElement);
    controls.target.set(0, 0.8, 0);
    controls.enablePan = false;
    controls.enableDamping = true;
    controls.minDistance = 8;
    controls.maxDistance = 17;
    controls.minPolarAngle = 0.25;
    controls.maxPolarAngle = 1.3;
    controls.enableZoom = false;
    controls.update();
    controls.saveState();
    reset.current = () => controls.reset();
    scene.add(new THREE.AmbientLight(0xe5eee6, 1.1));
    const key = new THREE.DirectionalLight(0xfff0d7, 3.2);
    key.position.set(-3, 9, 5);
    key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024);
    key.shadow.camera.left = -5; key.shadow.camera.right = 5;
    key.shadow.camera.top = 5; key.shadow.camera.bottom = -5;
    key.shadow.normalBias = 0.025;
    scene.add(key);
    const rimLight = new THREE.DirectionalLight(0xb9ddd5, 3);
    rimLight.position.set(5, 4, -4);
    scene.add(rimLight);
    const chrome = new THREE.MeshStandardMaterial({ color: '#9aa5a4', metalness: 0.85, roughness: 0.24 });
    const rubber = new THREE.MeshStandardMaterial({ color: '#161a18', roughness: 0.8 });
    const root = new THREE.Group();
    scene.add(root);
    const cylinder = (radius: number, height: number, material: THREE.Material, segments = 32) => {
      const mesh = new THREE.Mesh(new THREE.CylinderGeometry(radius, radius, height, segments), material);
      mesh.castShadow = true; mesh.receiveShadow = true;
      return mesh;
    };
    const rod = (from: THREE.Vector3, to: THREE.Vector3, radius = 0.022) => {
      const mesh = cylinder(radius, from.distanceTo(to), chrome, 8);
      mesh.position.copy(from).add(to).multiplyScalar(0.5);
      mesh.quaternion.setFromUnitVectors(new THREE.Vector3(0, 1, 0), to.clone().sub(from).normalize());
      root.add(mesh);
    };
    const stand = (x: number, y: number, z: number) => {
      rod(new THREE.Vector3(x, 0.08, z), new THREE.Vector3(x, y, z));
      for (let leg = 0; leg < 3; leg++) {
        const angle = leg * Math.PI * 2 / 3;
        rod(new THREE.Vector3(x, 0.35, z), new THREE.Vector3(x + Math.cos(angle) * 0.4, 0.04, z + Math.sin(angle) * 0.4), 0.018);
      }
    };
    const hoop = (radius: number, material: THREE.Material) => {
      const mesh = new THREE.Mesh(new THREE.TorusGeometry(radius, 0.025, 8, 64), material);
      mesh.rotation.x = Math.PI / 2;
      return mesh;
    };
    const drum = (id: Drum, x: number, y: number, z: number, radius: number, depth: number, color: string, kick = false) => {
      const group = new THREE.Group();
      group.position.set(x, y, z);
      if (kick) group.rotation.x = Math.PI / 2;
      const shell = new THREE.MeshStandardMaterial({ color, roughness: 0.3, metalness: 0.4 });
      group.add(cylinder(radius, depth, shell, 48));
      const head = new THREE.MeshStandardMaterial({ color: kick ? '#afb7a8' : '#c8cbbf', roughness: 0.73, metalness: 0.08, emissive: '#d5ed83', emissiveIntensity: 0 });
      const skin = cylinder(radius - 0.025, 0.018, head, 48);
      skin.position.y = depth / 2 + 0.005;
      group.add(skin);
      for (const sign of [-1, 1]) {
        const rim = hoop(radius, chrome);
        rim.position.y = sign * depth / 2;
        group.add(rim);
      }
      for (let lug = 0; lug < 8; lug++) {
        const angle = lug / 8 * Math.PI * 2;
        const tension = cylinder(0.025, depth + 0.04, chrome, 8);
        tension.position.set(Math.cos(angle) * (radius + 0.01), 0, Math.sin(angle) * (radius + 0.01));
        group.add(tension);
      }
      if (kick) {
        const textureCanvas = document.createElement('canvas');
        textureCanvas.width = 512; textureCanvas.height = 512;
        const context = textureCanvas.getContext('2d')!;
        context.fillStyle = '#b7bdae'; context.fillRect(0, 0, 512, 512);
        context.fillStyle = '#243128'; context.textAlign = 'center';
        context.font = 'bold 43px sans-serif'; context.fillText('BACKBEAT', 256, 235);
        context.font = '18px sans-serif'; context.fillText('DRUM STUDIO', 256, 270);
        context.beginPath(); context.arc(350, 370, 45, 0, Math.PI * 2); context.fill();
        const texture = new THREE.CanvasTexture(textureCanvas);
        texture.colorSpace = THREE.SRGBColorSpace;
        texture.center.set(0.5, 0.5); texture.rotation = Math.PI / 2;
        head.map = texture;
        rod(new THREE.Vector3(x - 0.6, y - 0.35, z), new THREE.Vector3(x - 0.9, 0.04, z + 0.3));
        rod(new THREE.Vector3(x + 0.6, y - 0.35, z), new THREE.Vector3(x + 0.9, 0.04, z + 0.3));
      } else stand(x, y - depth / 2, z);
      group.traverse(object => { object.userData.drum = id; });
      root.add(group);
      instruments.current[id] = { group, head, energy: 0, rotation: group.rotation.z, cymbal: false };
    };
    const cymbal = (id: Drum, x: number, y: number, z: number, radius: number, tilt: number) => {
      stand(x, y + 0.1, z);
      const group = new THREE.Group();
      group.position.set(x, y, z); group.rotation.z = tilt;
      const head = new THREE.MeshStandardMaterial({ color: '#ba9649', metalness: 0.78, roughness: 0.33, side: THREE.DoubleSide, emissive: '#f0ce7d', emissiveIntensity: 0 });
      const profile = [new THREE.Vector2(0.025, 0.12), new THREE.Vector2(0.1, 0.12), new THREE.Vector2(0.18, 0.04), new THREE.Vector2(radius * 0.55, 0.012), new THREE.Vector2(radius, -0.02)];
      const disc = new THREE.Mesh(new THREE.LatheGeometry(profile, 64), head);
      disc.castShadow = true; disc.receiveShadow = true; group.add(disc);
      const grooves = new THREE.MeshStandardMaterial({ color: '#87713d', metalness: 0.65, roughness: 0.5 });
      for (let ring = 0.24; ring < radius; ring += 0.065) {
        const mesh = new THREE.Mesh(new THREE.TorusGeometry(ring, 0.0025, 3, 64), grooves);
        mesh.rotation.x = Math.PI / 2; mesh.position.y = 0.025 - ring * 0.05;
        group.add(mesh);
      }
      const top = cylinder(0.045, 0.06, rubber, 12); top.position.y = 0.15; group.add(top);
      if (id === 'hihat') { const lower = disc.clone(); lower.position.y = -0.09; group.add(lower); }
      group.traverse(object => { object.userData.drum = id; });
      root.add(group);
      instruments.current[id] = { group, head, energy: 0, rotation: tilt, cymbal: true };
    };
    drum('kick', 0, 0.82, 0.8, 0.79, 0.68, '#663b38', true);
    drum('snare', -1.12, 1.08, 0.93, 0.48, 0.25, '#8a9591');
    drum('tom1', -0.58, 1.67, -0.17, 0.43, 0.45, '#663b38');
    drum('tom2', 0.43, 1.67, -0.25, 0.48, 0.49, '#663b38');
    drum('floor', 1.37, 0.95, 0.51, 0.58, 0.63, '#663b38');
    cymbal('hihat', -1.95, 1.53, 0.35, 0.58, -0.06);
    cymbal('crash', -1.52, 2.43, -1.05, 0.78, 0.12);
    cymbal('ride', 1.58, 2.16, -0.91, 0.85, -0.19);
    const throne = cylinder(0.36, 0.15, rubber); throne.position.set(0, 0.85, -1.5); root.add(throne);
    stand(0, 0.8, -1.5);
    const floor = new THREE.Mesh(new THREE.CircleGeometry(3.55, 96), new THREE.MeshStandardMaterial({ color: '#171b1c', roughness: 1, metalness: 0 }));
    floor.rotation.x = -Math.PI / 2; floor.position.y = -0.02; floor.receiveShadow = true; scene.add(floor);
    const floorRing = new THREE.Mesh(new THREE.RingGeometry(3.54, 3.56, 128), new THREE.MeshBasicMaterial({ color: '#3a443c', side: THREE.DoubleSide }));
    floorRing.rotation.x = -Math.PI / 2; floorRing.position.y = -0.015; scene.add(floorRing);
    const resize = new ResizeObserver(() => {
      camera.aspect = element.clientWidth / element.clientHeight;
      camera.fov = camera.aspect < 1.3 ? 44 : 33;
      camera.updateProjectionMatrix();
      renderer.setSize(element.clientWidth, element.clientHeight);
    });
    resize.observe(element);
    const raycaster = new THREE.Raycaster();
    let pointerDown = { x: 0, y: 0 };
    const down = (event: PointerEvent) => { pointerDown = { x: event.clientX, y: event.clientY }; };
    const pick = (event: PointerEvent) => {
      if (Math.hypot(event.clientX - pointerDown.x, event.clientY - pointerDown.y) > 5) return;
      const rect = renderer.domElement.getBoundingClientRect();
      raycaster.setFromCamera(new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1, -(event.clientY - rect.top) / rect.height * 2 + 1), camera);
      const selected = raycaster.intersectObject(root, true).find(intersection => intersection.object.userData.drum);
      if (selected) handler.current(selected.object.userData.drum);
    };
    renderer.domElement.addEventListener('pointerdown', down);
    renderer.domElement.addEventListener('pointerup', pick);
    let visible = true;
    const intersection = new IntersectionObserver(entries => { visible = entries[0].isIntersecting; });
    intersection.observe(element);
    let frame = 0;
    let previous = 0;
    const animate = (time: number) => {
      frame = requestAnimationFrame(animate);
      if (!visible || document.hidden || time - previous < 30) return;
      previous = time;
      for (const instrument of Object.values(instruments.current)) {
        if (!instrument) continue;
        instrument.energy *= 0.78;
        instrument.head.emissiveIntensity = instrument.energy * 0.9;
        if (instrument.cymbal) instrument.group.rotation.z = instrument.rotation + Math.sin(time * 0.045) * instrument.energy * 0.11;
        else instrument.group.scale.setScalar(1 + instrument.energy * 0.018);
      }
      controls.update(); renderer.render(scene, camera);
    };
    frame = requestAnimationFrame(animate);
    return () => {
      cancelAnimationFrame(frame); resize.disconnect(); intersection.disconnect(); controls.dispose();
      renderer.domElement.removeEventListener('pointerdown', down);
      renderer.domElement.removeEventListener('pointerup', pick);
      scene.traverse(object => {
        if (object instanceof THREE.Mesh) {
          object.geometry.dispose();
          for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
            if ('map' in material && material.map instanceof THREE.Texture) material.map.dispose();
            material.dispose();
          }
        }
      });
      environment.dispose(); renderer.dispose(); renderer.domElement.remove(); instruments.current = {};
    };
  }, []);

  return <div className="kit-scene">
    <div ref={mount} className="kit-canvas" />
    {unavailable && <div className="kit-fallback">3D preview unavailable. The drum pads and MIDI player are still ready.</div>}
    <button className="icon-button scene-reset" title="Reset drum kit view" aria-label="Reset drum kit view" onClick={() => reset.current()}><RotateCcw size={15} /></button>
    <div className="kit-axis" aria-hidden="true"><span>01</span><i /><span>ACOUSTIC KIT</span></div>
  </div>;
});

