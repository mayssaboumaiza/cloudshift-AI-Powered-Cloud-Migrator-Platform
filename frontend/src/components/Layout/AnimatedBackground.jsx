import { useEffect, useRef } from "react";

const NODE_COUNT = 65;
const MAX_DIST   = 190;
const SPEED      = 0.45;

function rand(min, max) { return Math.random() * (max - min) + min; }

export default function AnimatedBackground() {
  const canvasRef = useRef(null);

  useEffect(() => {
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;

    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");

    const nodes = Array.from({ length: NODE_COUNT }, () => ({
      x:  rand(0, window.innerWidth),
      y:  rand(0, window.innerHeight),
      vx: rand(-SPEED, SPEED),
      vy: rand(-SPEED, SPEED),
      r:  rand(2.5, 4.5),
    }));

    let raf;

    function resize() {
      canvas.width  = window.innerWidth;
      canvas.height = window.innerHeight;
    }
    resize();
    window.addEventListener("resize", resize);

    function draw() {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      for (const n of nodes) {
        n.x += n.vx;
        n.y += n.vy;
        if (n.x < 0 || n.x > canvas.width)  n.vx *= -1;
        if (n.y < 0 || n.y > canvas.height) n.vy *= -1;
      }

      for (let i = 0; i < nodes.length; i++) {
        for (let j = i + 1; j < nodes.length; j++) {
          const dx = nodes[i].x - nodes[j].x;
          const dy = nodes[i].y - nodes[j].y;
          const d  = Math.sqrt(dx * dx + dy * dy);
          if (d < MAX_DIST) {
            const alpha = 1 - d / MAX_DIST;
            ctx.beginPath();
            ctx.moveTo(nodes[i].x, nodes[i].y);
            ctx.lineTo(nodes[j].x, nodes[j].y);
            ctx.strokeStyle = `rgba(14,165,233,${alpha * 0.80})`;
            ctx.lineWidth   = alpha * 1.2;
            ctx.stroke();
          }
        }
      }

      for (const n of nodes) {
        // glow ring
        const grd = ctx.createRadialGradient(n.x, n.y, 0, n.x, n.y, n.r * 3);
        grd.addColorStop(0,   "rgba(14,165,233,0.55)");
        grd.addColorStop(1,   "rgba(14,165,233,0)");
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r * 3, 0, Math.PI * 2);
        ctx.fillStyle = grd;
        ctx.fill();

        // solid core
        ctx.beginPath();
        ctx.arc(n.x, n.y, n.r, 0, Math.PI * 2);
        ctx.fillStyle = "rgba(14,165,233,0.90)";
        ctx.fill();
      }

      raf = requestAnimationFrame(draw);
    }

    draw();
    return () => {
      cancelAnimationFrame(raf);
      window.removeEventListener("resize", resize);
    };
  }, []);

  return <canvas ref={canvasRef} className="archon-bg-canvas" />;
}
