/**
 * Traffic globe.
 *
 * A restrained, abstract rendering: a graticule sphere with one spike per
 * active city, height by request volume and colour by threat. It is here
 * because geography is genuinely spatial data — not for decoration — and it
 * degrades to the country table beside it if WebGL is unavailable.
 *
 * Written directly against three.js (no react-three-fiber) to keep the bundle
 * small, and it disposes every geometry, material and the renderer on unmount.
 */

import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'

import { useUi } from '@/lib/store'
import type { GeoPoint } from '@/lib/types'

const RADIUS = 1

function toVector(lat: number, lon: number, radius = RADIUS): THREE.Vector3 {
  const phi = (90 - lat) * (Math.PI / 180)
  const theta = (lon + 180) * (Math.PI / 180)
  return new THREE.Vector3(
    -radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta),
  )
}

function graticule(segments = 64): THREE.BufferGeometry {
  const positions: number[] = []
  // Parallels every 15°, meridians every 15° — dense enough to read as a
  // sphere, sparse enough not to moiré.
  for (let lat = -75; lat <= 75; lat += 15) {
    for (let i = 0; i < segments; i += 1) {
      const a = toVector(lat, (i / segments) * 360 - 180)
      const b = toVector(lat, ((i + 1) / segments) * 360 - 180)
      positions.push(a.x, a.y, a.z, b.x, b.y, b.z)
    }
  }
  for (let lon = -180; lon < 180; lon += 15) {
    for (let i = 0; i < segments / 2; i += 1) {
      const a = toVector((i / (segments / 2)) * 180 - 90, lon)
      const b = toVector(((i + 1) / (segments / 2)) * 180 - 90, lon)
      positions.push(a.x, a.y, a.z, b.x, b.y, b.z)
    }
  }
  const geometry = new THREE.BufferGeometry()
  geometry.setAttribute('position', new THREE.Float32BufferAttribute(positions, 3))
  return geometry
}

export function Globe({ points, height = 420 }: { points: GeoPoint[]; height?: number }) {
  const theme = useUi((state) => state.theme)
  const containerRef = useRef<HTMLDivElement>(null)
  const pointsRef = useRef<GeoPoint[]>(points)
  const rebuildRef = useRef<((data: GeoPoint[]) => void) | null>(null)
  const [supported, setSupported] = useState(true)

  pointsRef.current = points

  useEffect(() => {
    const container = containerRef.current
    if (!container) return

    let renderer: THREE.WebGLRenderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'low-power' })
    } catch {
      setSupported(false)
      return
    }

    const width = container.clientWidth || 600
    renderer.setSize(width, height)
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2))
    container.appendChild(renderer.domElement)

    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(38, width / height, 0.1, 100)
    camera.position.set(0, 0.6, 3.1)
    camera.lookAt(0, 0, 0)

    const root = new THREE.Group()
    root.rotation.z = (-23.4 * Math.PI) / 180 // axial tilt, purely for character
    scene.add(root)

    // Design tokens are stored as space-separated channels ("10 12 15") so they
    // can be used with Tailwind's `<alpha-value>` syntax. THREE.Color cannot
    // parse CSS Color Level 4 `rgb(10 12 15)` — it silently falls back to white,
    // which rendered the entire globe as a white ball. Parse the channels
    // ourselves instead of handing three.js a string it does not understand.
    const styles = getComputedStyle(document.documentElement)
    const read = (name: string, fallback = 0x8899aa): THREE.Color => {
      const raw = styles.getPropertyValue(name).trim()
      const channels = raw.split(/[\s,]+/).map(Number).filter((n) => Number.isFinite(n))
      if (channels.length < 3) return new THREE.Color(fallback)
      return new THREE.Color(channels[0] / 255, channels[1] / 255, channels[2] / 255)
    }

    const accent = read('--accent', 0x3987e5)
    const critical = read('--critical', 0xd03b3b)
    const warn = read('--warn', 0xfab219)
    const lineColor = read('--line-strong', 0x2a323c)
    const bodyColor = read('--canvas', 0x0a0c0f)

    // Sphere body — nearly opaque so back-side spikes do not show through.
    const sphere = new THREE.Mesh(
      new THREE.SphereGeometry(RADIUS * 0.995, 48, 48),
      new THREE.MeshBasicMaterial({ color: bodyColor, transparent: true, opacity: 0.92 }),
    )
    root.add(sphere)

    const grid = new THREE.LineSegments(
      graticule(),
      new THREE.LineBasicMaterial({ color: lineColor, transparent: true, opacity: 0.55 }),
    )
    root.add(grid)

    const halo = new THREE.Mesh(
      new THREE.SphereGeometry(RADIUS * 1.035, 48, 48),
      new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.05, side: THREE.BackSide }),
    )
    root.add(halo)

    const markers = new THREE.Group()
    root.add(markers)

    function clearMarkers() {
      for (const child of [...markers.children]) {
        markers.remove(child)
        const mesh = child as THREE.Mesh
        mesh.geometry?.dispose()
        const material = mesh.material as THREE.Material | THREE.Material[]
        if (Array.isArray(material)) material.forEach((item) => item.dispose())
        else material?.dispose()
      }
    }

    function build(data: GeoPoint[]) {
      clearMarkers()
      for (const point of data.slice(0, 220)) {
        const position = toVector(point.lat, point.lon)
        const magnitude = 0.05 + Math.sqrt(Math.max(point.weight, 0.01)) * 0.42
        const color = point.hostile ? critical : point.error_rate > 0.15 ? warn : accent

        const spike = new THREE.Mesh(
          new THREE.CylinderGeometry(0.0055, 0.0055, magnitude, 6, 1, true),
          new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.78 }),
        )
        spike.position.copy(position.clone().multiplyScalar(1 + magnitude / 2))
        spike.lookAt(0, 0, 0)
        spike.rotateX(Math.PI / 2)
        markers.add(spike)

        const cap = new THREE.Mesh(
          new THREE.SphereGeometry(point.hostile ? 0.017 : 0.012, 8, 8),
          new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.95 }),
        )
        cap.position.copy(position.clone().multiplyScalar(1 + magnitude))
        markers.add(cap)
      }
    }

    build(pointsRef.current)
    rebuildRef.current = build

    // Pointer drag to spin; auto-rotation resumes shortly after release.
    let dragging = false
    let lastX = 0
    let lastY = 0
    let velocity = 0.0016
    let idleAt = 0

    const onDown = (event: PointerEvent) => {
      dragging = true
      lastX = event.clientX
      lastY = event.clientY
      renderer.domElement.setPointerCapture(event.pointerId)
    }
    const onMove = (event: PointerEvent) => {
      if (!dragging) return
      const deltaX = event.clientX - lastX
      const deltaY = event.clientY - lastY
      lastX = event.clientX
      lastY = event.clientY
      root.rotation.y += deltaX * 0.005
      root.rotation.x = Math.max(-0.7, Math.min(0.7, root.rotation.x + deltaY * 0.004))
      velocity = deltaX * 0.0008
      idleAt = performance.now() + 1400
    }
    const onUp = (event: PointerEvent) => {
      dragging = false
      renderer.domElement.releasePointerCapture?.(event.pointerId)
    }

    renderer.domElement.addEventListener('pointerdown', onDown)
    renderer.domElement.addEventListener('pointermove', onMove)
    renderer.domElement.addEventListener('pointerup', onUp)
    renderer.domElement.addEventListener('pointerleave', onUp)
    renderer.domElement.style.cursor = 'grab'
    renderer.domElement.style.touchAction = 'pan-y'

    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    let frame = 0
    let running = true

    const animate = () => {
      if (!running) return
      frame = requestAnimationFrame(animate)
      if (!dragging) {
        if (performance.now() > idleAt) velocity += (0.0016 - velocity) * 0.02
        root.rotation.y += reduced ? 0 : velocity
      }
      renderer.render(scene, camera)
    }
    animate()

    // Pause when off-screen — a spinning globe in a background tab is waste.
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting && !running) {
          running = true
          animate()
        } else if (!entry.isIntersecting) {
          running = false
          cancelAnimationFrame(frame)
        }
      },
      { threshold: 0.05 },
    )
    observer.observe(container)

    const resize = new ResizeObserver(() => {
      const nextWidth = container.clientWidth || width
      camera.aspect = nextWidth / height
      camera.updateProjectionMatrix()
      renderer.setSize(nextWidth, height)
    })
    resize.observe(container)

    return () => {
      running = false
      cancelAnimationFrame(frame)
      observer.disconnect()
      resize.disconnect()
      renderer.domElement.removeEventListener('pointerdown', onDown)
      renderer.domElement.removeEventListener('pointermove', onMove)
      renderer.domElement.removeEventListener('pointerup', onUp)
      renderer.domElement.removeEventListener('pointerleave', onUp)
      clearMarkers()
      sphere.geometry.dispose()
      ;(sphere.material as THREE.Material).dispose()
      grid.geometry.dispose()
      ;(grid.material as THREE.Material).dispose()
      halo.geometry.dispose()
      ;(halo.material as THREE.Material).dispose()
      renderer.dispose()
      container.removeChild(renderer.domElement)
      rebuildRef.current = null
    }
  }, [height, theme])

  useEffect(() => {
    rebuildRef.current?.(points)
  }, [points])

  if (!supported) {
    return (
      <div
        className="flex items-center justify-center rounded border border-line bg-raised/40 text-xs text-ink-3"
        style={{ height }}
      >
        WebGL is unavailable — use the country table below.
      </div>
    )
  }

  return (
    <div className="relative">
      <div ref={containerRef} style={{ height }} aria-hidden />
      <div className="pointer-events-none absolute bottom-2 left-3 flex items-center gap-3 text-2xs text-ink-3">
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-accent" /> normal
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-warn" /> elevated errors
        </span>
        <span className="flex items-center gap-1.5">
          <span className="h-1.5 w-1.5 rounded-full bg-critical" /> flagged origin
        </span>
        <span className="ml-2 opacity-70">drag to rotate</span>
      </div>
    </div>
  )
}
