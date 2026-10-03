# V1 sprint screen: synthetic field test

For testing the manual-time screen (`/v1/sprint`) on a real phone or tablet with **made-up
players only**. Not for real children: real child data stays blocked until the items in
"Not ready for real children" are closed.

## Start a throwaway server

```bash
python scripts/v1_synthetic_pilot_server.py                    # this computer only
python scripts/v1_synthetic_pilot_server.py --host <LAN-IP>    # a phone on the same Wi-Fi
```

The script builds a brand-new temporary database and upload folders (never the real ones),
seeds `Test Player A` (with the consent the screen needs), `Test Player B` (no consent),
two coaches and a reviewer, and prints the sign-in details once. Stopping it (Ctrl+C or
`kill`) deletes everything. Over Wi-Fi it uses plain http on your local network: use a
trusted network, stop it when done, and do not use a public tunnel.

Find the computer's LAN address on macOS with `ipconfig getifaddr en0`, then open
`http://<LAN-IP>:8765/v1/sprint` on the phone.

## Checklist (tick each on the device)

1. Sign in as `t.coach1`.
2. Open **Player Assessment: 20 m Sprint** from the Assessments page (or the address above).
3. Choose **Test Player A**. Start a new assessment.
4. **ADD ATTEMPT**. The attempt number is easy to see.
5. Tap the time box: the keyboard opens and the **SAVE TIME** button is still reachable.
6. Enter `4.25`, tap **SAVE TIME**. It shows `SAVED 4.25 sec`.
7. Reload the page. `4.25 sec` is still there.
8. Add another attempt and tap **SAVE TIME** twice quickly with `4.20`. Only one attempt and one time appear, and it reads `4.20`.
9. Add an attempt and use **TIMER PROBLEM / TIME NOT RECORDED**. No number is saved; the wording does not blame the player.
10. **CORRECT ENTRY** on an attempt: choose a reason and a second person. Original and current values both show.
11. Switch to Airplane mode, save a time, switch back: the message says it could not confirm, and pressing **SAVE TIME** again saves it once.
12. Rotate the phone. Nothing is cut off; no sideways scrolling.
13. Look at the screen outdoors in sunlight. Can you read the time and the player name?
14. Keyboard: if the decimal keypad on your phone has no `.` (some regions show `,`), note it. The screen deliberately refuses a comma.
15. Sign in as `t.reviewer`: the page is read-only (no save, add or correct buttons).
16. Choose **Test Player B** and start an assessment: you get the consent message and no way around it.

Write down anything slow, unclear or hard to tap. No part of this has been run on a real phone
yet by the build; the checklist is the test.

## Not ready for real children

PostgreSQL has never run V1; the consent purpose (`performance_tracking`) is a judgement, not a
frozen decision; a correction records a second person's name but that person does not approve it;
registration, billing, dataset and video privacy decisions are open.
