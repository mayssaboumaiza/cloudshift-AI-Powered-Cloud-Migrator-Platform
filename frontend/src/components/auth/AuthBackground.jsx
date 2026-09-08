import { useRef, useEffect } from "react";

export default function AuthBackground() {
  const canvasRef = useRef(null);
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    let W = canvas.width  = window.innerWidth;
    let H = canvas.height = window.innerHeight;
    const onResize = () => { W = canvas.width = window.innerWidth; H = canvas.height = window.innerHeight; };
    window.addEventListener("resize", onResize);

    const COLORS = ["#7dd3fc", "#60a5fa", "#93c5fd", "#38bdf8", "#67e8f9"];
    const nodes = Array.from({ length: 55 }, () => ({
      x: Math.random() * W, y: Math.random() * H,
      r: 2 + Math.random() * 2.5,
      dx: (Math.random() - 0.5) * 0.28, dy: (Math.random() - 0.5) * 0.28,
      color: COLORS[Math.floor(Math.random() * COLORS.length)],
      pulse: Math.random() * Math.PI * 2,
    }));

    let raf;
    const draw = () => {
      ctx.clearRect(0, 0, W, H);

      for (let i = 0; i < nodes.length; i++) for (let j = i + 1; j < nodes.length; j++) {
        const dx = nodes[i].x - nodes[j].x, dy = nodes[i].y - nodes[j].y;
        const d = Math.sqrt(dx * dx + dy * dy);
        if (d < 160) {
          ctx.beginPath();
          ctx.strokeStyle = `rgba(96,165,250,${0.55 * (1 - d / 160)})`;
          ctx.lineWidth = 1.2;
          ctx.moveTo(nodes[i].x, nodes[i].y); ctx.lineTo(nodes[j].x, nodes[j].y); ctx.stroke();
        }
      }

      for (const p of nodes) {
        p.x += p.dx; p.y += p.dy; p.pulse += 0.025;
        if (p.x < 0) p.x = W; if (p.x > W) p.x = 0;
        if (p.y < 0) p.y = H; if (p.y > H) p.y = 0;
        const pr = p.r * (1 + 0.18 * Math.sin(p.pulse));
        const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, pr * 6);
        g.addColorStop(0, p.color + "55");
        g.addColorStop(1, p.color + "00");
        ctx.beginPath(); ctx.fillStyle = g;
        ctx.arc(p.x, p.y, pr * 6, 0, Math.PI * 2); ctx.fill();
        ctx.beginPath();
        ctx.fillStyle = p.color + "cc";
        ctx.arc(p.x, p.y, pr, 0, Math.PI * 2); ctx.fill();
      }

      raf = requestAnimationFrame(draw);
    };
    draw();
    return () => { cancelAnimationFrame(raf); window.removeEventListener("resize", onResize); };
  }, []);

  return (
    <>
      <div style={{
        position: "fixed", inset: 0, zIndex: 0,
        background: "linear-gradient(135deg, #e0f2fe 0%, #bae6fd 35%, #dbeafe 70%, #e0f7fa 100%)",
      }} />
      <canvas ref={canvasRef} style={{
        position: "fixed", inset: 0, width: "100%", height: "100%",
        pointerEvents: "none", zIndex: 1,
      }} />
      <div style={{ position: "fixed", top: "5%",  left: "10%",  width: 520, height: 520, borderRadius: "50%", background: "radial-gradient(circle, rgba(56,189,248,0.20) 0%, transparent 65%)",  pointerEvents: "none", zIndex: 2 }} />
      <div style={{ position: "fixed", bottom: "5%", right: "8%", width: 460, height: 460, borderRadius: "50%", background: "radial-gradient(circle, rgba(96,165,250,0.18) 0%, transparent 65%)",  pointerEvents: "none", zIndex: 2 }} />
      <div style={{ position: "fixed", top: "45%", right: "22%", width: 320, height: 320, borderRadius: "50%", background: "radial-gradient(circle, rgba(125,211,252,0.16) 0%, transparent 65%)", pointerEvents: "none", zIndex: 2 }} />
    </>
  );
}
