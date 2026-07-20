"""Fixtures for the REST API contract ("golden") tests.

Mirrors tests/control/test_core_service_rest.py: a module-scoped simulated
Microscope shared across tests, with a fresh SquidCoreService + FastAPI
TestClient per test so job/fault/method state never leaks between contracts.

IMPORTANT: no fixture or test here may store a laser-AF reference on the
shared sim scope -- it would leak into the z_reference "no reference" contract.
"""

import pytest
from fastapi.testclient import TestClient

import control.microscope
import tests.control.test_stubs as ts
from squid_service.config import ServiceConfig
from squid_service.rest.app import create_app
from squid_service.service import SquidCoreService


@pytest.fixture(scope="module")
def sim_scope():
    scope = control.microscope.Microscope.build_from_global_config(True)
    yield scope
    scope.close()


@pytest.fixture()
def service(sim_scope, tmp_path):
    mpc = ts.get_test_multi_point_controller(sim_scope)
    return SquidCoreService(
        microscope=sim_scope,
        multipoint_controller=mpc,
        scan_coordinates=mpc.scanCoordinates,
        simulation=True,
        job_persist_path=tmp_path / "last_job.json",
        methods_dir=tmp_path / "methods",
    )


@pytest.fixture()
def client(service):
    app = create_app(service, ServiceConfig())
    return TestClient(app)


@pytest.fixture()
def first_channel(sim_scope):
    """The first channel name for the current objective on the sim scope."""
    objective = sim_scope.objective_store.current_objective
    return sim_scope.live_controller.get_channels(objective)[0].name
