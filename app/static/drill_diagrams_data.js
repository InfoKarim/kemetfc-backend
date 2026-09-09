/*
 * Structured diagram data for drill-library entries. Kept separate from
 * drill_diagram.js (the renderer) so new drills can be added here without
 * touching rendering code.
 *
 * `diagram: null` means the drill exists in the library card list but its
 * coaching diagram has not been built yet.
 */
(function () {
  const FIRST_TOUCH_GATES = {
    id: "first-touch-gates",
    name: "First-touch gates",
    description:
      "Players receive a pass and steer their first touch through a cone gate, changing the angle through a second gate before jogging back to the line.",
    viewBox: { width: 600, height: 380 },
    cones: [
      { id: "gate-a-1", x: 350, y: 150 },
      { id: "gate-a-2", x: 350, y: 250 },
      { id: "gate-b-1", x: 480, y: 90 },
      { id: "gate-b-2", x: 480, y: 170 },
    ],
    players: [
      { id: "p1", x: 90, y: 200, label: "P1" },
      { id: "p2", x: 230, y: 200, label: "P2" },
    ],
    balls: [{ id: "ball-start", x: 90, y: 222 }],
    paths: [
      {
        id: "pass",
        type: "ball_pass",
        points: [
          { x: 104, y: 200 },
          { x: 216, y: 200 },
        ],
        step: 2,
        label: "Pass",
      },
      {
        id: "touch-gate-a",
        type: "dribble",
        points: [
          { x: 244, y: 200 },
          { x: 350, y: 200 },
        ],
        step: 3,
        label: "1st touch",
      },
      {
        id: "touch-gate-b",
        type: "dribble",
        points: [
          { x: 350, y: 200 },
          { x: 480, y: 130 },
        ],
        step: 4,
        label: "Change angle",
      },
      {
        id: "jog-back",
        type: "player_move",
        points: [
          { x: 480, y: 130 },
          { x: 300, y: 260 },
          { x: 230, y: 214 },
        ],
        step: 5,
        label: "Jog back",
      },
    ],
    steps: [
      "Player 2 stands just ahead of Player 1, facing Gate A.",
      "Player 1 passes the ball firmly into Player 2's path.",
      "Player 2 takes a positive first touch through Gate A.",
      "Player 2 opens the body and dribbles through Gate B, changing the angle.",
      "Player 2 jogs back to the start; players rotate after every 3 turns.",
    ],
  };

  window.DRILL_LIBRARY_DIAGRAMS = {
    "first-touch-gates": {
      key: "first-touch-gates",
      name: "First-touch gates",
      summary: "Players receive and take a positive first touch through the gates.",
      diagram: FIRST_TOUCH_GATES,
    },
    "passing-pairs": {
      key: "passing-pairs",
      name: "Passing pairs",
      summary: "Build passing quality and movement in pairs.",
      diagram: null,
    },
    "control-turn": {
      key: "control-turn",
      name: "Control & turn",
      summary: "Receive, control and turn into space with a positive next action.",
      diagram: null,
    },
  };
})();
