"""All /v1 REST routers. Handlers are sync functions (FastAPI runs them in a
threadpool), calling the service facade directly — same threading position the
old socket threads occupied."""

from typing import Optional

from fastapi import APIRouter, Request, Response

from squid_service import faults as F
from squid_service.models import (
    AbortRequest,
    AcquireRequest,
    AcquisitionRequest,
    AutofocusCorrectRequest,
    AutofocusRunRequest,
    ChannelSelectRequest,
    DebugSettingsRequest,
    ExposureRequest,
    InitializeRequest,
    IntensityRequest,
    LaserAfImageRequest,
    MethodCreateRequest,
    MethodUpdateRequest,
    MoveRequest,
    ObjectiveRequest,
    PythonExecRequest,
)


def _svc(request: Request):
    return request.app.state.service


def _not_implemented(name: str):
    raise F.FaultError(
        F.make_fault(
            F.FaultCategory.PROTOCOL,
            F.PROTOCOL_NOT_IMPLEMENTED,
            f"{name} is reserved for a future version",
        )
    )


def build_routers():
    meta = APIRouter(prefix="/v1", tags=["meta"])

    @meta.get("/healthz")
    def healthz():
        return {"alive": True}

    @meta.get("/sample_formats")
    def sample_formats(request: Request):
        return _svc(request).sample_formats()

    system = APIRouter(prefix="/v1/system", tags=["system"])

    @system.post("/initialize")
    def initialize(request: Request, body: Optional[InitializeRequest] = None):
        home = body.home if body is not None else False
        return _svc(request).initialize(home=home)

    @system.post("/reset")
    def reset(request: Request):
        return _svc(request).reset()

    @system.get("/status")
    def status(request: Request):
        return _svc(request).status()

    @system.get("/heartbeat")
    def heartbeat(request: Request):
        return _svc(request).heartbeat()

    @system.get("/capabilities")
    def capabilities(request: Request):
        return _svc(request).capabilities()

    @system.get("/version")
    def version(request: Request):
        return _svc(request).version()

    @system.get("/auth_status")
    def auth_status(request: Request):
        config = request.app.state.config
        return {"auth_enabled": config.auth_enabled, "bind_to_tls": False, "scheme": "bearer"}

    @system.get("/faults")
    def faults(request: Request, since: int = 0, limit: int = 100):
        return _svc(request).faults_since(since, limit)

    @system.post("/reserve")
    def reserve():
        _not_implemented("reserve")

    @system.post("/release")
    def release():
        _not_implemented("release")

    @system.post("/shutdown")
    def shutdown():
        _not_implemented("shutdown")

    motion = APIRouter(prefix="/v1/motion", tags=["motion"])

    @motion.get("/position")
    def position(request: Request):
        return _svc(request).get_position()

    @motion.post("/move")
    def move(request: Request, body: MoveRequest):
        return _svc(request).move(body)

    @motion.post("/home")
    def home(request: Request):
        return _svc(request).home()

    imaging = APIRouter(prefix="/v1/imaging", tags=["imaging"])

    @imaging.get("/channels")
    def channels(request: Request):
        return _svc(request).list_channels()

    @imaging.post("/channel")
    def select_channel(request: Request, body: ChannelSelectRequest):
        return _svc(request).select_channel(body.name)

    @imaging.post("/exposure")
    def exposure(request: Request, body: ExposureRequest):
        return _svc(request).set_exposure(body)

    @imaging.post("/intensity")
    def intensity(request: Request, body: IntensityRequest):
        return _svc(request).set_intensity(body)

    @imaging.post("/illumination/on")
    def illumination_on(request: Request):
        return _svc(request).illumination(True)

    @imaging.post("/illumination/off")
    def illumination_off(request: Request):
        return _svc(request).illumination(False)

    @imaging.get("/objectives")
    def objectives(request: Request):
        return _svc(request).get_objectives()

    @imaging.get("/objective")
    def get_objective(request: Request):
        return {"objective": _svc(request).get_objectives()["current"]}

    @imaging.post("/objective")
    def set_objective(request: Request, body: ObjectiveRequest):
        return _svc(request).set_objective(body.name)

    @imaging.post("/acquire")
    def acquire(request: Request, body: AcquireRequest = AcquireRequest()):
        return _svc(request).acquire(body)

    @imaging.post("/live/start")
    def live_start(request: Request):
        return _svc(request).live(True)

    @imaging.post("/live/stop")
    def live_stop(request: Request):
        return _svc(request).live(False)

    autofocus = APIRouter(prefix="/v1/autofocus", tags=["autofocus"])

    @autofocus.post("/run")
    def af_run(request: Request, body: AutofocusRunRequest = AutofocusRunRequest()):
        return _svc(request).autofocus_run(body)

    @autofocus.get("/status")
    def af_status(request: Request):
        return _svc(request).autofocus_status()

    @autofocus.post("/store_reference")
    def af_store_reference(request: Request):
        return _svc(request).autofocus_store_reference()

    @autofocus.post("/correct")
    def af_correct(request: Request, body: AutofocusCorrectRequest = AutofocusCorrectRequest()):
        return _svc(request).autofocus_correct(body)

    @autofocus.post("/acquire_image")
    def af_acquire_image(request: Request, body: LaserAfImageRequest = LaserAfImageRequest()):
        return _svc(request).autofocus_acquire_image(body)

    acquisitions = APIRouter(prefix="/v1/acquisitions", tags=["acquisitions"])

    @acquisitions.post("/preflight")
    def preflight(request: Request, body: AcquisitionRequest):
        return _svc(request).preflight(body)

    @acquisitions.post("", status_code=202)
    def create_acquisition(request: Request, response: Response, body: AcquisitionRequest):
        handle = _svc(request).start_acquisition(body)
        response.headers["Location"] = f"/v1/jobs/{handle['job_id']}"
        return handle

    jobs = APIRouter(prefix="/v1/jobs", tags=["jobs"])

    @jobs.get("/last")  # MUST precede /{job_id}
    def last_job(request: Request):
        return _svc(request).last_job()

    @jobs.get("/{job_id}")
    def get_job(request: Request, job_id: str):
        return _svc(request).get_job(job_id)

    @jobs.post("/{job_id}/abort")
    def abort_job(request: Request, job_id: str, body: Optional[AbortRequest] = None):
        timeout_s = body.timeout_s if body is not None else 60.0
        return _svc(request).abort_job(job_id, timeout_s=timeout_s)

    @jobs.post("/{job_id}/emergency_stop")
    def emergency_stop(job_id: str):
        _not_implemented("emergency_stop")

    methods = APIRouter(prefix="/v1/methods", tags=["methods"])

    @methods.get("")
    def list_methods(request: Request):
        return _svc(request).list_methods()

    @methods.get("/{name}")
    def get_method(request: Request, name: str):
        return _svc(request).get_method(name)

    @methods.post("", status_code=201)
    def create_method(request: Request, body: MethodCreateRequest):
        return _svc(request).create_method(body.name, body.config)

    @methods.put("/{name}")
    def update_method(request: Request, name: str, body: MethodUpdateRequest):
        return _svc(request).update_method(name, body.config)

    @methods.delete("/{name}")
    def delete_method(request: Request, name: str):
        return _svc(request).delete_method(name)

    @methods.post("/{name}/validate")
    def validate_method(request: Request, name: str):
        return _svc(request).validate_method(name)

    debug = APIRouter(prefix="/v1/debug", tags=["debug"])

    @debug.post("/python_exec")
    def python_exec(request: Request, body: PythonExecRequest):
        return _svc(request).python_exec(body.code)

    @debug.get("/python_exec/status")
    def python_exec_status(request: Request):
        return _svc(request).python_exec_status()

    @debug.get("/settings")
    def get_debug_settings(request: Request):
        return _svc(request).debug_settings()

    @debug.post("/settings")
    def set_debug_settings(request: Request, body: DebugSettingsRequest):
        return _svc(request).set_debug_settings(body)

    return [meta, system, motion, imaging, autofocus, acquisitions, jobs, methods, debug]
