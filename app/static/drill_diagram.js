/*
 * Reusable drill-diagram renderer.
 *
 * Renders a coaching-diagram SVG (pitch, cones, players, balls, movement
 * paths, step numbers, legend) from plain structured data. The data shape
 * is deliberately separate from this renderer — see drill_diagrams_data.js
 * for the actual drill content — so new drills can be added without
 * touching this file.
 *
 * Data shape:
 * {
 *   id: string,
 *   name: string,
 *   description: string,
 *   viewBox: { width, height },
 *   cones: [{ id, x, y }],
 *   players: [{ id, x, y, label }],
 *   balls: [{ id, x, y }],
 *   paths: [{
 *     id, type: "player_move" | "ball_pass" | "dribble",
 *     points: [{x,y}, ...],   // polyline, 2+ points
 *     step: number | null,    // step number badge, placed at the path midpoint
 *     label: string | null,   // short caption near the path
 *   }],
 *   steps: [string, ...],     // ordered, plain-English execution steps
 * }
 */
(function () {
  const SVG_NS = "http://www.w3.org/2000/svg";
  const COLORS = {
    pitchFill: "#1c5238",
    pitchLine: "rgba(244, 247, 250, .28)",
    cone: "#f5893a",
    coneOutline: "#8a3f10",
    player: "#12233c",
    playerLabel: "#f4f7fa",
    ball: "#f4f7fa",
    ballLine: "#12233c",
    moveStroke: "#f4f7fa",
    passStroke: "#f5c45d",
    dribbleStroke: "#8fe36b",
    stepBadgeFill: "#12233c",
    stepBadgeText: "#f4f7fa",
    label: "#f4f7fa",
  };

  function el(tag, attrs) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      node.setAttribute(key, value);
    }
    return node;
  }

  // The geometric midpoint between the path's start and end — NOT
  // `points[Math.floor(points.length / 2)]`, which for a 2-point path
  // (every straight pass/dribble segment) just returns the start point.
  function midpoint(points) {
    const first = points[0];
    const last = points[points.length - 1];
    return { x: (first.x + last.x) / 2, y: (first.y + last.y) / 2 };
  }

  function pointsToLinePath(points) {
    return points
      .map((point, index) => `${index === 0 ? "M" : "L"} ${point.x} ${point.y}`)
      .join(" ");
  }

  // A gentle sine wave along the straight line between the first and last
  // point, used for dribble paths so a "player moves the ball" segment
  // reads differently from a plain pass or run at a glance. The wave phase
  // is driven by real distance traveled (not by sample index), so it does
  // not collapse to zero regardless of how many samples are drawn.
  function pointsToWavyPath(points, amplitude = 9, wavelength = 30) {
    const start = points[0];
    const end = points[points.length - 1];
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const length = Math.hypot(dx, dy) || 1;
    const nx = -dy / length;
    const ny = dx / length;
    const samples = Math.max(10, Math.round(length / 8));

    let d = "";
    for (let i = 0; i <= samples; i += 1) {
      const t = i / samples;
      const distance = t * length;
      const wave = Math.sin((distance / wavelength) * 2 * Math.PI) * amplitude;
      const x = start.x + dx * t + nx * wave;
      const y = start.y + dy * t + ny * wave;
      d += i === 0 ? `M ${x} ${y}` : ` L ${x} ${y}`;
    }
    return d;
  }

  function pathStyleFor(type) {
    switch (type) {
      case "ball_pass":
        return { stroke: COLORS.passStroke, dash: "8 7", wavy: false, marker: "arrow-pass" };
      case "dribble":
        return { stroke: COLORS.dribbleStroke, dash: null, wavy: true, marker: "arrow-dribble" };
      case "player_move":
      default:
        return { stroke: COLORS.moveStroke, dash: null, wavy: false, marker: "arrow-move" };
    }
  }

  function buildDefs(svg) {
    const defs = el("defs", {});
    const markers = [
      { id: "arrow-move", fill: COLORS.moveStroke },
      { id: "arrow-pass", fill: COLORS.passStroke },
      { id: "arrow-dribble", fill: COLORS.dribbleStroke },
    ];
    for (const marker of markers) {
      const markerEl = el("marker", {
        id: marker.id,
        viewBox: "0 0 10 10",
        refX: "8",
        refY: "5",
        markerWidth: "7",
        markerHeight: "7",
        orient: "auto-start-reverse",
      });
      markerEl.append(el("path", { d: "M 0 0 L 10 5 L 0 10 z", fill: marker.fill }));
      defs.append(markerEl);
    }
    svg.append(defs);
  }

  function drawPitch(svg, viewBox) {
    const pad = 14;
    svg.append(
      el("rect", {
        x: pad,
        y: pad,
        width: viewBox.width - pad * 2,
        height: viewBox.height - pad * 2,
        rx: 16,
        fill: COLORS.pitchFill,
      })
    );
    svg.append(
      el("rect", {
        x: pad,
        y: pad,
        width: viewBox.width - pad * 2,
        height: viewBox.height - pad * 2,
        rx: 16,
        fill: "none",
        stroke: COLORS.pitchLine,
        "stroke-width": 2,
      })
    );
  }

  function drawCone(svg, cone) {
    const group = el("g", { transform: `translate(${cone.x}, ${cone.y})` });
    group.append(
      el("path", {
        d: "M 0 -11 L 8 9 L -8 9 Z",
        fill: COLORS.cone,
        stroke: COLORS.coneOutline,
        "stroke-width": 1.5,
      })
    );
    svg.append(group);
  }

  function drawPlayer(svg, player) {
    const group = el("g", { transform: `translate(${player.x}, ${player.y})` });
    group.append(el("circle", { r: 13, fill: COLORS.player, stroke: COLORS.ball, "stroke-width": 2 }));
    group.append(
      el("text", {
        x: 0,
        y: 4,
        "text-anchor": "middle",
        "font-size": 11,
        "font-weight": 800,
        fill: COLORS.playerLabel,
      })
    );
    group.lastChild.textContent = player.label || "";
    svg.append(group);
  }

  function drawBall(svg, ball) {
    const group = el("g", { transform: `translate(${ball.x}, ${ball.y})` });
    group.append(el("circle", { r: 6, fill: COLORS.ball, stroke: COLORS.ballLine, "stroke-width": 1.5 }));
    svg.append(group);
  }

  function drawPath(svg, path) {
    const style = pathStyleFor(path.type);
    const d = style.wavy ? pointsToWavyPath(path.points) : pointsToLinePath(path.points);
    const attrs = {
      d,
      fill: "none",
      stroke: style.stroke,
      "stroke-width": 3,
      "stroke-linecap": "round",
      "marker-end": `url(#${style.marker})`,
    };
    if (style.dash) attrs["stroke-dasharray"] = style.dash;
    svg.append(el("path", attrs));

    if (path.label) {
      const at = midpoint(path.points);
      const label = el("text", {
        x: at.x,
        y: at.y - 12,
        "text-anchor": "middle",
        "font-size": 10.5,
        "font-weight": 700,
        fill: COLORS.label,
      });
      label.textContent = path.label;
      svg.append(label);
    }

    if (path.step != null) {
      // Offset the badge along the path's own direction, away from its
      // start point — the start often sits on a player/cone marker, which
      // would otherwise hide the number underneath it.
      const from = path.points[0];
      const towards = path.points[1] || path.points[path.points.length - 1];
      const dx = towards.x - from.x;
      const dy = towards.y - from.y;
      const segmentLength = Math.hypot(dx, dy) || 1;
      const badgeOffset = 22;
      const at = {
        x: from.x + (dx / segmentLength) * badgeOffset,
        y: from.y + (dy / segmentLength) * badgeOffset,
      };
      const badge = el("g", { transform: `translate(${at.x}, ${at.y})` });
      badge.append(el("circle", { r: 9, fill: COLORS.stepBadgeFill, stroke: style.stroke, "stroke-width": 2 }));
      const text = el("text", {
        x: 0,
        y: 3.5,
        "text-anchor": "middle",
        "font-size": 10,
        "font-weight": 800,
        fill: COLORS.stepBadgeText,
      });
      text.textContent = String(path.step);
      badge.append(text);
      svg.append(badge);
    }
  }

  function describe(data) {
    const parts = [`${data.name}. ${data.description}`];
    if (data.steps && data.steps.length) {
      parts.push("Steps: " + data.steps.map((step, i) => `${i + 1}) ${step}`).join(" "));
    }
    return parts.join(" ");
  }

  function render(container, data, options) {
    if (!container || !data) return null;
    container.replaceChildren();

    const viewBox = data.viewBox || { width: 600, height: 380 };
    const svg = el("svg", {
      viewBox: `0 0 ${viewBox.width} ${viewBox.height}`,
      role: "img",
      "aria-label": describe(data),
      style: "width: 100%; height: auto; display: block;",
    });

    buildDefs(svg);
    drawPitch(svg, viewBox);

    for (const cone of data.cones || []) drawCone(svg, cone);
    for (const path of data.paths || []) drawPath(svg, path);
    for (const player of data.players || []) drawPlayer(svg, player);
    for (const ball of data.balls || []) drawBall(svg, ball);

    container.append(svg);

    if (options && options.withLegend) {
      container.append(buildLegend());
    }

    return svg;
  }

  function buildLegend() {
    const legend = document.createElement("div");
    legend.className = "drill-diagram-legend";
    const items = [
      { swatch: "solid", color: COLORS.moveStroke, label: "Player movement" },
      { swatch: "dashed", color: COLORS.passStroke, label: "Ball pass" },
      { swatch: "wavy", color: COLORS.dribbleStroke, label: "Dribble (player + ball)" },
    ];
    for (const item of items) {
      const row = document.createElement("span");
      row.className = "drill-diagram-legend-item";
      const swatch = document.createElement("svg");
      swatch.setAttribute("viewBox", "0 0 40 12");
      swatch.setAttribute("width", "40");
      swatch.setAttribute("height", "12");
      let d = "M 2 6 L 38 6";
      if (item.swatch === "wavy") d = "M 2 6 Q 12 -2 20 6 T 38 6";
      const path = document.createElementNS(SVG_NS, "path");
      path.setAttribute("d", d);
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", item.color);
      path.setAttribute("stroke-width", "3");
      path.setAttribute("stroke-linecap", "round");
      if (item.swatch === "dashed") path.setAttribute("stroke-dasharray", "5 4");
      swatch.append(path);
      const label = document.createElement("span");
      label.textContent = item.label;
      row.append(swatch, label);
      legend.append(row);
    }
    return legend;
  }

  window.DrillDiagram = { render, describe };
})();
