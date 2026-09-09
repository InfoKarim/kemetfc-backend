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

  const PASSING_PAIRS = {
    id: "passing-pairs",
    name: "Passing pairs",
    description:
      "Two players pass and move into space after every pass, keeping the ball moving with quick, accurate touches.",
    viewBox: { width: 600, height: 380 },
    players: [
      { id: "p1", x: 120, y: 220, label: "P1" },
      { id: "p2", x: 480, y: 160, label: "P2" },
    ],
    balls: [{ id: "ball-start", x: 120, y: 242 }],
    paths: [
      {
        id: "pass-1",
        type: "ball_pass",
        points: [
          { x: 135, y: 217 },
          { x: 465, y: 163 },
        ],
        step: 2,
        label: "Pass",
      },
      {
        id: "move-1",
        type: "player_move",
        points: [
          { x: 132, y: 228 },
          { x: 255, y: 295 },
        ],
        step: 3,
        label: "Move",
      },
      {
        id: "pass-2",
        type: "ball_pass",
        points: [
          { x: 468, y: 168 },
          { x: 270, y: 293 },
        ],
        step: 4,
        label: "Return pass",
      },
      {
        id: "move-2",
        type: "player_move",
        points: [
          { x: 466, y: 150 },
          { x: 352, y: 88 },
        ],
        step: 5,
        label: "Move",
      },
    ],
    steps: [
      "Player 1 and Player 2 face each other about 15 yards apart.",
      "Player 1 passes firmly to Player 2.",
      "Player 1 jogs into space to receive the next pass.",
      "Player 2 controls and passes to Player 1's new position.",
      "Player 2 jogs into space to reset for the next repetition.",
    ],
  };

  const CONTROL_AND_TURN = {
    id: "control-turn",
    name: "Control & turn",
    description:
      "A player receives, takes a heavy touch to turn around a cone, then drives into the open space beyond it.",
    viewBox: { width: 600, height: 380 },
    cones: [{ id: "turn-cone", x: 340, y: 200 }],
    players: [
      { id: "p1", x: 90, y: 200, label: "P1" },
      { id: "p2", x: 240, y: 200, label: "P2" },
    ],
    balls: [{ id: "ball-start", x: 90, y: 222 }],
    paths: [
      {
        id: "pass",
        type: "ball_pass",
        points: [
          { x: 104, y: 200 },
          { x: 226, y: 200 },
        ],
        step: 2,
        label: "Pass",
      },
      {
        id: "turn",
        type: "dribble",
        points: [
          { x: 240, y: 200 },
          { x: 340, y: 260 },
        ],
        step: 3,
        label: "Turn",
      },
      {
        id: "drive",
        type: "dribble",
        points: [
          { x: 340, y: 260 },
          { x: 480, y: 120 },
        ],
        step: 4,
        label: "Drive",
      },
      {
        id: "jog-back",
        type: "player_move",
        points: [
          { x: 480, y: 120 },
          { x: 300, y: 260 },
          { x: 240, y: 200 },
        ],
        step: 5,
        label: "Jog back",
      },
    ],
    steps: [
      "Player 2 stands a few yards from the cone, facing Player 1.",
      "Player 1 passes the ball into Player 2's feet.",
      "Player 2 takes a heavy touch to turn around the cone.",
      "Player 2 drives into the open space on the far side.",
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
      diagram: PASSING_PAIRS,
    },
    "control-turn": {
      key: "control-turn",
      name: "Control & turn",
      summary: "Receive, control and turn into space with a positive next action.",
      diagram: CONTROL_AND_TURN,
    },
  };
})();
