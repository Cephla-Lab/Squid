# PR #540 filter-wheel silent-fail repro

GUI tool that reproduces the bug fixed by Cephla-Lab/Squid#540 and confirms the fix works.
Run against a real Teensy with a Squid filter wheel connected.

## Launch

    cd software
    conda activate squid
    python tools/pr540_filter_wheel_repro_gui.py

## Typical session

1. Choose port, click **Connect**. Firmware version + expected-verdict banner appears.
2. (Optional) Use the **Firmware** panel to build + flash a different ref (e.g. pre-fix `fae3aa0a` to reproduce the bug, then `origin/master` to confirm the fix).
3. Click **Init filter wheel + measure baseline**.
4. Click **Run A**, **Run B**, **Run C**, **Run gate test** — or **Run all scenarios**.
5. Compare verdicts against the expected matrix:
   - Pre-fix firmware (v < 1.2): OBSERVED-BUG on A/B/C, GATE-NOT-PRESENT on Gate.
   - Post-fix firmware (v ≥ 1.2): PASS on all.

## What each scenario does

- **A** — pre-INIT MOVE_W. Sends MOVE_W and MOVE_W2 *before* INITFILTERWHEEL. Pre-fix firmware silently acks COMPLETED; post-fix firmware reports CMD_EXECUTION_ERROR and the host raises CommandAborted in <100 ms.
- **B** — rapid back-to-back MOVE_W. Bursts of MOVE_W commands without inter-move settle, on an initialized wheel. Pre-fix firmware may silently ack faster than physical motion can complete; post-fix firmware fast-fails the second move.
- **C** — soak. Many single-slot MOVE_W moves; catches sporadic instances of the same bug.
- **Gate** — host-side only. Verifies SquidFilterWheel raises RuntimeError when constructed against firmware v < 1.2. GATE-NOT-PRESENT on the pre-fix host branch.

See `worktrees/docs/2026-05-24-pr-540-repro-gui-design.md` for full design.
