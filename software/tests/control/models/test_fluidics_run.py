from control.models.fluidics_run import AttemptRecord, RunCursor, RunManifest, StepRecord, TecState


def test_manifest_round_trips_through_json():
    m = RunManifest(
        run_name="liver",
        run_dir="/data/liver_2026",
        protocol_name="demo",
        protocol_sha256="abc",
        cursor=RunCursor(step=1, attempt=2, sequence=3),
        steps=[
            StepRecord(index=0, kind="fluidics", round="setup", label="setup", row_indices=[0]),
            StepRecord(
                index=1,
                kind="imaging",
                round="R01",
                label="image",
                row_indices=[3],
                attempts=[AttemptRecord(attempt=1, outcome="user_abort", folder="R01_image")],
            ),
        ],
        tec=TecState(targets=[37.0], output_enabled=[True]),
        pid=42,
        heartbeat_at=1.0,
        started_at=0.5,
    )
    again = RunManifest.model_validate_json(m.model_dump_json())
    assert again == m
    assert again.is_terminal is False
    assert again.step(1).attempts[0].folder == "R01_image"
    again.status = "finished"
    assert again.is_terminal is True
