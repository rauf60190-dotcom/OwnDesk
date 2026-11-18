import { useEffect, useRef, type ReactNode } from "react";
import "./PointerSurface.css";

// Original OwnDesk effect: a soft pointer light with orbiting dots and click rings.
// No third-party animation implementation or additional runtime dependency.
export function PointerSurface({ children }: { children: ReactNode }) {
  const surface = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const host = surface.current, layer = canvas.current;
    if (!host || !layer) return;
    const context = layer.getContext("2d");
    if (!context) return;
    const preference = matchMedia("(prefers-reduced-motion: no-preference) and (hover: hover) and (pointer: fine)");
    let allowed = preference.matches, visible = true, frame = 0, width = 1, height = 1;
    let x = 0, y = 0, targetX = 0, targetY = 0, lastPointer = -Infinity, lastFrame = 0;
    const rings: { x: number; y: number; start: number }[] = [];

    const clear = () => context.clearRect(0, 0, width, height);
    const stop = () => { cancelAnimationFrame(frame); frame = 0; lastFrame = 0; clear(); };
    const draw = (now: number) => {
      frame = 0;
      if (!allowed || !visible || document.hidden) { stop(); return; }
      clear();
      const fade = Math.max(0, 1 - (now - lastPointer) / 900);
      const elapsed = Math.min(50, lastFrame ? now - lastFrame : 16);
      lastFrame = now;
      const follow = 1 - Math.exp(-elapsed / 80);
      x += (targetX - x) * follow;
      y += (targetY - y) * follow;
      if (fade > 0) {
        const light = context.createRadialGradient(x, y, 0, x, y, 125);
        light.addColorStop(0, `rgba(95, 138, 255, ${fade * 0.16})`);
        light.addColorStop(1, "rgba(95, 138, 255, 0)");
        context.fillStyle = light;
        context.fillRect(x - 125, y - 125, 250, 250);
        for (let dot = 0; dot < 28; dot++) {
          const angle = dot * Math.PI * 2 / 28 + now / 3100;
          const radius = 26 + (dot % 5) * 9 + Math.sin(now / 650 + dot) * 4;
          context.fillStyle = `rgba(169, 195, 255, ${fade * (0.3 + (dot % 3) * 0.12)})`;
          context.beginPath();
          context.arc(x + Math.cos(angle) * radius, y + Math.sin(angle) * radius, 1 + (dot % 3) * 0.3, 0, Math.PI * 2);
          context.fill();
        }
      }
      for (let index = rings.length - 1; index >= 0; index--) {
        const ring = rings[index], age = (now - ring.start) / 650;
        if (age >= 1) { rings.splice(index, 1); continue; }
        context.strokeStyle = `rgba(140, 179, 255, ${(1 - age) * 0.48})`;
        context.lineWidth = 1.5;
        context.beginPath(); context.arc(ring.x, ring.y, 8 + age * 85, 0, Math.PI * 2); context.stroke();
      }
      if (fade > 0 || rings.length) frame = requestAnimationFrame(draw);
      else lastFrame = 0;
    };
    const schedule = () => { if (allowed && visible && !document.hidden && !frame) frame = requestAnimationFrame(draw); };
    const point = (event: PointerEvent) => {
      const rect = host.getBoundingClientRect();
      targetX = event.clientX - rect.left; targetY = event.clientY - rect.top;
      if (!lastFrame) { x = targetX; y = targetY; }
    };
    const move = (event: PointerEvent) => {
      if (!allowed || event.pointerType === "touch") return;
      point(event); lastPointer = performance.now(); schedule();
    };
    const click = (event: PointerEvent) => {
      if (!allowed || event.pointerType === "touch") return;
      point(event); rings.push({ x: targetX, y: targetY, start: performance.now() });
      if (rings.length > 6) rings.shift();
      lastPointer = performance.now(); schedule();
    };
    const resize = () => {
      const rect = host.getBoundingClientRect();
      width = Math.max(1, rect.width); height = Math.max(1, rect.height);
      const scale = Math.min(devicePixelRatio || 1, 1.5, Math.sqrt(1_800_000 / (width * height)));
      layer.width = Math.max(1, Math.floor(width * scale)); layer.height = Math.max(1, Math.floor(height * scale));
      context.setTransform(scale, 0, 0, scale, 0, 0);
      schedule();
    };
    const updatePreference = () => {
      allowed = preference.matches; layer.hidden = !allowed;
      if (!allowed) { rings.length = 0; lastPointer = -Infinity; stop(); }
    };
    const updateVisibility = () => {
      if (document.hidden) stop(); else schedule();
    };
    const observer = new ResizeObserver(resize);
    const intersection = new IntersectionObserver(([entry]) => {
      visible = entry.isIntersecting;
      if (!visible) stop(); else schedule();
    });
    observer.observe(host); intersection.observe(host);
    host.addEventListener("pointermove", move, { passive: true });
    host.addEventListener("pointerdown", click, { passive: true });
    preference.addEventListener("change", updatePreference);
    document.addEventListener("visibilitychange", updateVisibility);
    updatePreference(); resize();
    return () => {
      stop(); observer.disconnect(); intersection.disconnect();
      host.removeEventListener("pointermove", move); host.removeEventListener("pointerdown", click);
      preference.removeEventListener("change", updatePreference);
      document.removeEventListener("visibilitychange", updateVisibility);
    };
  }, []);

  return <div className="own-pointer-surface" ref={surface}>
    <canvas className="own-pointer-light" ref={canvas} aria-hidden="true" />
    <div className="own-pointer-content">{children}</div>
  </div>;
}
