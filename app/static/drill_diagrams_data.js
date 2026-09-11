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

  const ONE_V_ONE_TAKE_ON = {
    id: "one-v-one-take-on",
    name: "1v1 take-on",
    description:
      "An attacker drives at a defender, uses a change of pace to beat them, and drives through an end gate.",
    viewBox: { width: 600, height: 380 },
    cones: [
      { id: "gate-1", x: 480, y: 140 },
      { id: "gate-2", x: 480, y: 260 },
    ],
    players: [
      { id: "a", x: 120, y: 200, label: "A" },
      { id: "d", x: 300, y: 200, label: "D" },
    ],
    balls: [{ id: "ball-start", x: 120, y: 222 }],
    paths: [
      {
        id: "drive-at",
        type: "dribble",
        points: [
          { x: 134, y: 200 },
          { x: 300, y: 280 },
        ],
        step: 2,
        label: "Drive at defender",
      },
      {
        id: "beat",
        type: "dribble",
        points: [
          { x: 300, y: 280 },
          { x: 465, y: 255 },
        ],
        step: 3,
        label: "Beat defender",
      },
      {
        id: "jog-back",
        type: "player_move",
        points: [
          { x: 465, y: 255 },
          { x: 250, y: 260 },
          { x: 120, y: 200 },
        ],
        step: 4,
        label: "Reset",
      },
    ],
    steps: [
      "Player A (attacker) faces Player D (defender) with the ball, about 10 yards apart.",
      "Player A drives directly at the defender with the ball.",
      "A change of pace takes Player A past the defender and through the gate.",
      "Player A jogs back with the ball; swap attacker and defender every 3 reps.",
    ],
  };

  const RONDO_POSSESSION = {
    id: "rondo-possession",
    name: "Rondo (4v1 possession)",
    description:
      "Four players keep the ball moving around a square with quick, accurate passes while one defender in the middle tries to win it back.",
    viewBox: { width: 600, height: 380 },
    players: [
      { id: "p1", x: 150, y: 110, label: "P1" },
      { id: "p2", x: 450, y: 110, label: "P2" },
      { id: "p3", x: 450, y: 290, label: "P3" },
      { id: "p4", x: 150, y: 290, label: "P4" },
      { id: "d", x: 300, y: 200, label: "D" },
    ],
    balls: [{ id: "ball-start", x: 150, y: 132 }],
    paths: [
      {
        id: "pass-1",
        type: "ball_pass",
        points: [
          { x: 164, y: 110 },
          { x: 436, y: 110 },
        ],
        step: 2,
        label: "Pass",
      },
      {
        id: "pass-2",
        type: "ball_pass",
        points: [
          { x: 450, y: 124 },
          { x: 450, y: 276 },
        ],
        step: 3,
        label: "Pass",
      },
      {
        id: "pass-3",
        type: "ball_pass",
        points: [
          { x: 436, y: 290 },
          { x: 164, y: 290 },
        ],
        step: 4,
        label: "Pass",
      },
      {
        id: "pass-4",
        type: "ball_pass",
        points: [
          { x: 150, y: 276 },
          { x: 150, y: 124 },
        ],
        step: 5,
        label: "Pass",
      },
    ],
    steps: [
      "Four players form a square around one defender in the middle.",
      "Player 1 passes along the outside to Player 2.",
      "Player 2 switches it to Player 3.",
      "Player 3 keeps it moving to Player 4.",
      "Player 4 returns it to Player 1; the defender rotates in after every 10 completed passes.",
    ],
  };

  const SHOOTING_TECHNIQUE = {
    id: "shooting-technique",
    name: "Shooting technique",
    description:
      "A player receives a pass at the edge of the box and strikes a first-time shot low between the posts.",
    viewBox: { width: 600, height: 380 },
    cones: [
      { id: "post-1", x: 520, y: 150 },
      { id: "post-2", x: 520, y: 250 },
    ],
    players: [
      { id: "p1", x: 100, y: 280, label: "P1" },
      { id: "p2", x: 260, y: 280, label: "P2" },
    ],
    balls: [{ id: "ball-start", x: 100, y: 302 }],
    paths: [
      {
        id: "pass",
        type: "ball_pass",
        points: [
          { x: 114, y: 280 },
          { x: 246, y: 280 },
        ],
        step: 2,
        label: "Pass",
      },
      {
        id: "shot",
        type: "ball_pass",
        points: [
          { x: 274, y: 280 },
          { x: 500, y: 200 },
        ],
        step: 3,
        label: "Shot",
      },
      {
        id: "follow-in",
        type: "player_move",
        points: [
          { x: 260, y: 280 },
          { x: 480, y: 220 },
        ],
        step: 4,
        label: "Follow in",
      },
    ],
    steps: [
      "Player 2 checks away from goal, ready to receive on the edge of the box.",
      "Player 1 plays a firm pass into Player 2's stride.",
      "Player 2 strikes a first-time shot low between the posts.",
      "Player 2 follows the shot in for a rebound; rotate after every 5 shots.",
    ],
  };

  const SWITCHING_PLAY = {
    id: "switching-play",
    name: "Switching play",
    description:
      "A long diagonal pass switches the ball from one wide area to the other; the receiving player controls and drives forward.",
    viewBox: { width: 600, height: 380 },
    players: [
      { id: "p1", x: 100, y: 120, label: "P1" },
      { id: "p2", x: 500, y: 280, label: "P2" },
    ],
    balls: [{ id: "ball-start", x: 100, y: 142 }],
    paths: [
      {
        id: "switch",
        type: "ball_pass",
        points: [
          { x: 114, y: 128 },
          { x: 486, y: 272 },
        ],
        step: 2,
        label: "Switch",
      },
      {
        id: "drive",
        type: "dribble",
        points: [
          { x: 500, y: 280 },
          { x: 500, y: 170 },
        ],
        step: 3,
        label: "Drive forward",
      },
    ],
    steps: [
      "Player 1 receives on the left touchline with room to switch the play.",
      "Player 1 drives a long diagonal pass to Player 2 on the far side.",
      "Player 2 controls out of the air and drives forward into space.",
      "Player 2 delivers into the box or carries on; repeat switching sides each rep.",
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
    "one-v-one-take-on": {
      key: "one-v-one-take-on",
      name: "1v1 take-on",
      summary: "Beat a defender one-on-one with a change of pace and drive through the gate.",
      diagram: ONE_V_ONE_TAKE_ON,
    },
    "rondo-possession": {
      key: "rondo-possession",
      name: "Rondo (4v1 possession)",
      summary: "Keep the ball moving around a square under pressure from a middle defender.",
      diagram: RONDO_POSSESSION,
    },
    "shooting-technique": {
      key: "shooting-technique",
      name: "Shooting technique",
      summary: "Receive at the edge of the box and strike a first-time shot on goal.",
      diagram: SHOOTING_TECHNIQUE,
    },
    "switching-play": {
      key: "switching-play",
      name: "Switching play",
      summary: "Switch the ball long across the pitch and drive forward into space.",
      diagram: SWITCHING_PLAY,
    },
  };
})();
