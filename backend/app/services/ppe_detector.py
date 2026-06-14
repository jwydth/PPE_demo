import time
from pathlib import Path

from PIL import Image

from app.core.config import BACKEND_DIR, settings
from app.schemas.detection import (
    DetectionResponse,
    PersonResult,
    TrackingOverlay,
    TrackingOverlayFrame,
    VideoProcessingResponse,
    VideoSummary,
)
from app.schemas.violation import ZoneViolation
from app.services.zone_service import (
    check_zone_incursion,
    get_person_foot_point,
    load_zones,
    record_zone_violation,
)
from .detection.response_builder import _build_response
from .detection.video_utils import _extract_result_boxes, _video_metadata
from .detection.violation_recorder import ViolationCase, _record_violation_case, _save_violation_snapshot
from .detection.worker_tracker import WorkerState, _update_worker_status


def _select_inference_device(preferred_device: str) -> str:
    requested = (preferred_device or "auto").strip().lower()
    if requested == "cpu":
        return "cpu"

    if requested not in {"auto", "cuda", "gpu"} and not (
        requested.startswith("cuda:") or requested.isdigit()
    ):
        return "cpu"

    try:
        import torch
    except Exception:
        return "cpu"

    if not torch.cuda.is_available():
        return "cpu"

    device_count = torch.cuda.device_count()
    if requested.isdigit():
        device_index = int(requested)
    elif requested.startswith("cuda:"):
        try:
            device_index = int(requested.split(":", 1)[1])
        except ValueError:
            device_index = 0
    else:
        device_index = 0

    if device_index >= device_count:
        return "cpu"

    return f"cuda:{device_index}"


class PPEDetector:
    def __init__(self) -> None:
        self.model = None
        self.device = _select_inference_device(settings.INFERENCE_DEVICE)
        self._load_model()

    def _load_model(self) -> None:
        model_path = Path(settings.MODEL_PATH).expanduser()
        if not model_path.is_absolute():
            model_path = BACKEND_DIR / model_path
        model_path = model_path.resolve()

        if not model_path.exists():
            return

        try:
            from ultralytics import YOLO
            self.model = YOLO(str(model_path))
        except Exception:
            pass

    def predict(self, image: Image.Image) -> DetectionResponse:
        if self.model is None:
            return self._mock_predict(image)
        return self._real_predict(image)

    def process_video(
        self,
        video_path: Path,
        video_name: str,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
    ) -> VideoProcessingResponse:
        if self.model is None:
            return self._mock_process_video(
                video_path, video_name, enable_ppe=enable_ppe, enable_zone=enable_zone
            )
        return self._real_process_video(
            video_path, video_name, enable_ppe=enable_ppe, enable_zone=enable_zone
        )

    def _real_predict(self, image: Image.Image) -> DetectionResponse:
        start = time.perf_counter()
        results = self.model(
            image,
            conf=settings.CONFIDENCE_THRESHOLD,
            device=self.device,
            verbose=False,
        )

        persons: list[dict] = []
        helmets: list[dict] = []
        vests: list[dict] = []
        for result in results:
            fp, fh, fv = _extract_result_boxes(result)
            persons.extend(fp)
            helmets.extend(fh)
            vests.extend(fv)

        elapsed_ms = (time.perf_counter() - start) * 1000
        return _build_response(persons, helmets, vests, elapsed_ms)

    def _real_process_video(
        self,
        video_path: Path,
        video_name: str,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
    ) -> VideoProcessingResponse:
        fps, total_frames = _video_metadata(video_path)
        start = time.perf_counter()
        stride = max(1, settings.VIDEO_FRAME_STRIDE)

        cases: list[ViolationCase] = []
        workers: list[WorkerState] = []
        zone_violations_list: list[ZoneViolation] = []
        confirmed_aspect_ratios: list[float] = []
        candidate_violations = 0
        processed_frames = 0
        frame_width: int | None = None
        frame_height: int | None = None
        overlay_frames: list[TrackingOverlayFrame] = []

        zones = load_zones(video_name) if enable_zone else []

        results = self.model.track(
            source=str(video_path),
            stream=True,
            persist=True,
            conf=settings.CONFIDENCE_THRESHOLD,
            tracker=settings.VIDEO_TRACKER,
            classes=[0, 1, 2],
            vid_stride=stride,
            device=self.device,
            verbose=False,
        )

        for processed_frames, result in enumerate(results, start=1):
            frame_index = (processed_frames - 1) * stride
            persons, helmets, vests = _extract_result_boxes(result)
            response = _build_response(persons, helmets, vests, 0.0)
            frame = result.orig_img.copy()
            frame_height, frame_width = frame.shape[:2]
            used_worker_ids: set[int] = set()
            overlay_person_ids: set[tuple[str, int]] = set()

            for person in response.persons:
                decision = _update_worker_status(
                    workers=workers,
                    person=person,
                    frame_index=frame_index,
                    fps=fps,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    used_worker_ids=used_worker_ids,
                )
                worker = decision["worker"]
                if enable_ppe:
                    candidate_violations += int(decision["candidate"])

                track_camera_zone_view_id: int | None = None
                track_physical_zone_id: int | None = None
                track_zone_name: str | None = None
                track_zone_type: str | None = None

                if zones:
                    test_point = get_person_foot_point(person, frame_width, frame_height)
                    incursion_zones = check_zone_incursion(zones, test_point)
                    incursion_camera_zone_view_ids = {
                        z.camera_zone_view_id for z in incursion_zones
                    }

                    for zone in zones:
                        camera_zone_view_id = zone.camera_zone_view_id
                        in_zone = camera_zone_view_id in incursion_camera_zone_view_ids

                        if zone.zone_type == "WALKWAY":
                            if in_zone:
                                worker.zone_dwell[camera_zone_view_id] = 0
                            else:
                                worker.zone_dwell[camera_zone_view_id] = (
                                    worker.zone_dwell.get(camera_zone_view_id, 0)
                                    + (stride / fps)
                                )
                                if (
                                    worker.zone_dwell[camera_zone_view_id] > zone.threshold
                                    and camera_zone_view_id not in worker.reported_zones
                                ):
                                    zv = record_zone_violation(
                                        worker_state=worker,
                                        zone=zone,
                                        frame=frame,
                                        person=person,
                                        video_name=video_name,
                                        frame_index=frame_index,
                                        save_snapshot_fn=_save_violation_snapshot,
                                    )
                                    if zv:
                                        zone_violations_list.append(zv)
                        else:
                            if in_zone:
                                worker.zone_dwell[camera_zone_view_id] = (
                                    worker.zone_dwell.get(camera_zone_view_id, 0)
                                    + (stride / fps)
                                )
                                if (
                                    worker.zone_dwell[camera_zone_view_id] > zone.threshold
                                    and camera_zone_view_id not in worker.reported_zones
                                ):
                                    zv = record_zone_violation(
                                        worker_state=worker,
                                        zone=zone,
                                        frame=frame,
                                        person=person,
                                        video_name=video_name,
                                        frame_index=frame_index,
                                        save_snapshot_fn=_save_violation_snapshot,
                                    )
                                    if zv:
                                        zone_violations_list.append(zv)

                    for zone in incursion_zones:
                        if zone.zone_type == "RESTRICTED":
                            track_camera_zone_view_id = zone.camera_zone_view_id
                            track_physical_zone_id = zone.physical_zone_id
                            track_zone_name = zone.zone_name
                            track_zone_type = "RESTRICTED"
                            break
                    if track_zone_type is None:
                        walkway_zones = [z for z in zones if z.zone_type == "WALKWAY"]
                        if walkway_zones and not any(
                            z.camera_zone_view_id in incursion_camera_zone_view_ids
                            for z in walkway_zones
                        ):
                            wz = walkway_zones[0]
                            track_camera_zone_view_id = wz.camera_zone_view_id
                            track_physical_zone_id = wz.physical_zone_id
                            track_zone_name = wz.zone_name
                            track_zone_type = "WALKWAY"

                _append_tracking_overlay_frame(
                    overlay_frames=overlay_frames,
                    seen_person_ids=overlay_person_ids,
                    person=person,
                    decision=decision,
                    frame_index=frame_index,
                    fps=fps,
                    camera_zone_view_id=track_camera_zone_view_id,
                    physical_zone_id=track_physical_zone_id,
                    zone_name=track_zone_name,
                    zone_type=track_zone_type,
                )

                missing_to_report = decision["missing_to_report"]
                if not enable_ppe or not missing_to_report:
                    continue

                _record_violation_case(
                    cases=cases,
                    frame=frame,
                    person=person,
                    worker=worker,
                    missing=missing_to_report,
                    video_name=video_name,
                    frame_index=frame_index,
                    confirmed_aspect_ratios=confirmed_aspect_ratios,
                )

        elapsed_ms = (time.perf_counter() - start) * 1000
        duration_seconds = total_frames / fps if fps > 0 else 0.0
        reports = [case.report for case in cases]

        return VideoProcessingResponse(
            summary=VideoSummary(
                video_name=video_name,
                total_frames=total_frames,
                processed_frames=processed_frames,
                fps=round(fps, 2),
                duration_seconds=round(duration_seconds, 2),
                unique_violations=len(cases),
                candidate_violations=candidate_violations,
                inference_ms=round(elapsed_ms, 2),
            ),
            reports=reports,
            zone_violations=zone_violations_list,
            tracking_overlay=TrackingOverlay(
                fps=round(fps, 2),
                stride=stride,
                frame_width=frame_width,
                frame_height=frame_height,
                frames=overlay_frames,
            ),
        )

    def _mock_predict(self, image: Image.Image) -> DetectionResponse:
        start = time.perf_counter()
        time.sleep(0.06)

        w, h = image.size

        def px(rel_box: tuple[float, float, float, float]) -> dict:
            rx1, ry1, rx2, ry2 = rel_box
            return {"x1": rx1 * w, "y1": ry1 * h, "x2": rx2 * w, "y2": ry2 * h}

        persons = [
            {**px((0.05, 0.02, 0.40, 0.98)), "conf": 0.96},
            {**px((0.55, 0.04, 0.95, 0.96)), "conf": 0.91},
        ]
        helmets = [{**px((0.10, 0.03, 0.35, 0.20)), "conf": 0.94}]
        vests = [{**px((0.08, 0.22, 0.38, 0.68)), "conf": 0.89}]

        elapsed_ms = (time.perf_counter() - start) * 1000
        return _build_response(persons, helmets, vests, elapsed_ms)

    def _mock_process_video(
        self,
        video_path: Path,
        video_name: str,
        *,
        enable_ppe: bool = True,
        enable_zone: bool = True,
    ) -> VideoProcessingResponse:
        import cv2

        fps, total_frames = _video_metadata(video_path)
        cap = cv2.VideoCapture(str(video_path))
        if not cap.isOpened():
            raise ValueError("Could not decode the uploaded video.")

        start = time.perf_counter()
        stride = max(1, settings.VIDEO_FRAME_STRIDE)
        frame_index = 0
        processed_frames = 0
        cases: list[ViolationCase] = []
        workers: list[WorkerState] = []
        confirmed_aspect_ratios: list[float] = []
        candidate_violations = 0
        frame_width: int | None = None
        frame_height: int | None = None
        overlay_frames: list[TrackingOverlayFrame] = []
        zones = load_zones(video_name) if enable_zone else []

        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if frame_index % stride != 0:
                frame_index += 1
                continue

            processed_frames += 1
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            response = self._mock_predict(Image.fromarray(rgb))
            frame_height, frame_width = frame.shape[:2]
            used_worker_ids: set[int] = set()
            overlay_person_ids: set[tuple[str, int]] = set()

            for person in response.persons:
                track_id = person.person_id
                person.track_id = track_id
                decision = _update_worker_status(
                    workers=workers,
                    person=person,
                    frame_index=frame_index,
                    fps=fps,
                    frame_width=frame_width,
                    frame_height=frame_height,
                    used_worker_ids=used_worker_ids,
                )
                if enable_ppe:
                    candidate_violations += int(decision["candidate"])

                track_zone_id: int | None = None
                track_zone_name: str | None = None
                track_zone_type: str | None = None

                if zones:
                    test_point = get_person_foot_point(person, frame_width, frame_height)
                    incursion_zones = check_zone_incursion(zones, test_point)
                    incursion_zone_ids = {z.zone_id for z in incursion_zones}

                    for zone in incursion_zones:
                        if zone.zone_type == "RESTRICTED":
                            track_zone_id = zone.zone_id
                            track_zone_name = zone.zone_name
                            track_zone_type = "RESTRICTED"
                            break
                    if track_zone_type is None:
                        walkway_zones = [z for z in zones if z.zone_type == "WALKWAY"]
                        if walkway_zones and not any(
                            z.zone_id in incursion_zone_ids for z in walkway_zones
                        ):
                            wz = walkway_zones[0]
                            track_zone_id = wz.zone_id
                            track_zone_name = wz.zone_name
                            track_zone_type = "WALKWAY"

                _append_tracking_overlay_frame(
                    overlay_frames=overlay_frames,
                    seen_person_ids=overlay_person_ids,
                    person=person,
                    decision=decision,
                    frame_index=frame_index,
                    fps=fps,
                    include_ppe=enable_ppe,
                    physical_zone_id=track_zone_id,
                    zone_name=track_zone_name,
                    zone_type=track_zone_type,
                )
                missing_to_report = decision["missing_to_report"]
                if not enable_ppe or not missing_to_report:
                    continue

                _record_violation_case(
                    cases=cases,
                    frame=frame,
                    person=person,
                    worker=decision["worker"],
                    missing=missing_to_report,
                    video_name=video_name,
                    frame_index=frame_index,
                    confirmed_aspect_ratios=confirmed_aspect_ratios,
                )

            frame_index += 1

        cap.release()
        elapsed_ms = (time.perf_counter() - start) * 1000
        duration_seconds = total_frames / fps if fps > 0 else 0.0
        reports = [case.report for case in cases]

        return VideoProcessingResponse(
            summary=VideoSummary(
                video_name=video_name,
                total_frames=total_frames,
                processed_frames=processed_frames,
                fps=round(fps, 2),
                duration_seconds=round(duration_seconds, 2),
                unique_violations=len(cases),
                candidate_violations=candidate_violations,
                inference_ms=round(elapsed_ms, 2),
            ),
            reports=reports,
            tracking_overlay=TrackingOverlay(
                fps=round(fps, 2),
                stride=stride,
                frame_width=frame_width,
                frame_height=frame_height,
                frames=overlay_frames,
            ),
        )


def _append_tracking_overlay_frame(
    *,
    include_ppe: bool = True,
    overlay_frames: list[TrackingOverlayFrame],
    seen_person_ids: set[tuple[str, int]],
    person: PersonResult,
    decision: dict,
    frame_index: int,
    fps: float,
    camera_zone_view_id: int | None = None,
    physical_zone_id: int | None = None,
    zone_name: str | None = None,
    zone_type: str | None = None,
) -> None:
    person_key = (
        ("track", person.track_id)
        if person.track_id is not None
        else ("person", person.person_id)
    )
    if person_key in seen_person_ids:
        return
    seen_person_ids.add(person_key)

    missing_equipment = (
        [
            equipment.label
            for equipment in person.equipment
            if equipment.status == "violation"
        ]
        if include_ppe
        else []
    )
    has_zone_violation = zone_type in {"RESTRICTED", "WALKWAY"}
    worker = decision.get("worker")
    worker_status = getattr(worker, "status", "unknown")
    if missing_equipment or has_zone_violation:
        status = "violation"
    elif decision.get("unknown") or worker_status == "unknown":
        status = "unknown"
    elif person.compliant or not include_ppe:
        status = "compliant"
    else:
        status = "unknown"

    overlay_frames.append(
        TrackingOverlayFrame(
            frame_index=frame_index,
            time_seconds=frame_index / fps if fps > 0 else 0.0,
            track_id=person.track_id,
            person_id=person.person_id,
            bbox=person.bbox,
            confidence=person.confidence,
            compliant=person.compliant and not has_zone_violation,
            missing_equipment=missing_equipment,
            status=status,
            zone_id=camera_zone_view_id,
            camera_zone_view_id=camera_zone_view_id,
            physical_zone_id=physical_zone_id,
            zone_name=zone_name,
            zone_type=zone_type,
        )
    )
