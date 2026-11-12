"""Camera workers: one thread per camera. Each worker runs YOLO + ByteTrack, confirms violations
over several frames, creates incidents (screenshot + 10s video clip), and publishes a shared
MJPEG frame so any number of browser tabs can watch the same camera."""
import os
import shutil
import subprocess
import threading
import time
from collections import deque
from datetime import datetime, timedelta

import cv2
import numpy as np

from backend import ai_review, alerts, config, db
from backend.events import bus
from backend.services import incident_service

GRACE_SECONDS = 5.0   # a violation that disappears for longer than this is forgotten

# Restricted zone: a person whose feet are inside the polygon drawn for the camera is an intruder.
ZONE_VIOLATION = "restricted_zone"
PERSON_NAMES = {"person", "worker", "human"}


def parse_zone(text):
    """'[[0.1,0.2],[0.5,0.2],[0.5,0.8]]' -> list of (x, y). Anything invalid means: no zone."""
    try:
        import json
        pts = json.loads(text) if text else []
        pts = [(float(x), float(y)) for x, y in pts]
        return pts if len(pts) >= 3 else []
    except Exception:
        return []


def _ffmpeg():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return None


def _write_clip(frames, filename, code):
    """frames = list of JPEG bytes. Browser-playable H.264 when ffmpeg is available."""
    import numpy as np
    if len(frames) < 5:
        return
    first = cv2.imdecode(np.frombuffer(frames[0], np.uint8), cv2.IMREAD_COLOR)
    h, w = first.shape[:2]
    raw = os.path.join(config.CLIPS_DIR, "raw_" + filename)
    out = os.path.join(config.CLIPS_DIR, filename)
    writer = cv2.VideoWriter(raw, cv2.VideoWriter_fourcc(*"mp4v"), 10, (w, h))
    for jpg in frames:
        img = cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_COLOR)
        if img is not None:
            writer.write(img)
    writer.release()
    exe = _ffmpeg()
    if exe:
        try:
            subprocess.run([exe, "-y", "-i", raw, "-vcodec", "libx264", "-pix_fmt", "yuv420p",
                            "-movflags", "+faststart", out], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, timeout=120, check=True)
            os.remove(raw)
        except Exception as e:
            print("ffmpeg transcode failed, keeping raw clip:", e)
            os.replace(raw, out)
    else:
        os.replace(raw, out)
    db.update_incident_fields(code, clip=filename)
    bus.publish({"type": "incident_updated", "incident_id": code})


class CameraWorker(threading.Thread):
    def __init__(self, cam):
        super().__init__(daemon=True, name=f"camera-{cam['id']}")
        self.cam = dict(cam)
        self.stop_event = threading.Event()
        self.status = "starting"
        self.error = ""
        self.fps = 0.0
        self.latest_jpeg = None
        self.frame_id = 0
        self.model = None
        self.use_track = True
        self.state = {}        # (track_id, class) -> violation state
        self.class_last = {}   # class -> last incident time
        self.last_dets = []
        self.buffer = deque()  # (timestamp, small jpeg) for clips
        self.last_buf = 0.0
        self.pending = []      # clips still collecting "after" frames
        self.required = {x.strip().lower() for x in (cam.get("required_ppe") or "").split(",") if x.strip()}
        self.zone = parse_zone(cam.get("restricted_zone"))
        self.last_raw = None   # newest clean frame, used by the zone editor

    # ------------------------------------------------------------------ lifecycle
    def stop(self):
        self.stop_event.set()

    def _load_model(self):
        try:
            if not os.path.exists(config.MODEL_PATH):
                raise FileNotFoundError(f"YOLO model not found: {config.MODEL_PATH}")
            from ultralytics import YOLO
            self.model = YOLO(config.MODEL_PATH)
            try:   # class list for the retraining pack
                with open(os.path.join(config.TRAINING_DIR, "classes.txt"), "w") as f:
                    f.write("\n".join(str(self.model.names[i]) for i in sorted(self.model.names)))
            except Exception:
                pass
            return True
        except Exception as e:
            self.status, self.error = "model_error", str(e)
            print(f"[{self.cam['name']}] model error: {e}")
            return False

    def _open(self):
        src = str(self.cam["source"]).strip()
        if src.isdigit():
            # one physical webcam cannot be opened by two cameras at the same time
            for w in list(manager.workers.values()):
                if w is not self and str(w.cam["source"]).strip() == src and w.status == "online":
                    self.error = f"Webcam {src} is already used by camera '{w.cam['name']}'"
                    return None
            idx = int(src)
            backends = [cv2.CAP_DSHOW, cv2.CAP_MSMF, cv2.CAP_ANY] if os.name == "nt" else [cv2.CAP_ANY]
            for be in backends:
                cap = cv2.VideoCapture(idx, be)
                if cap.isOpened():
                    ok, _ = cap.read()      # a backend that opens but cannot deliver frames is useless
                    if ok:
                        return cap
                cap.release()
            return None
        cap = cv2.VideoCapture(src)
        return cap if cap.isOpened() else None

    def run(self):
        if not self._load_model():
            return
        while not self.stop_event.is_set():
            self.error = ""
            cap = self._open()
            if cap is None:
                self.status = "offline"
                self.error = self.error or "Could not open camera source (is another app using the webcam?)"
                self.stop_event.wait(5)
                continue
            self.error = ""
            try:
                self._loop(cap)
            except Exception as e:
                self.status, self.error = "error", str(e)
                print(f"[{self.cam['name']}] worker error: {e}")
            finally:
                cap.release()
            self.stop_event.wait(2)
        self.status = "stopped"

    # ------------------------------------------------------------------ main loop
    def _loop(self, cap):
        is_file = os.path.isfile(str(self.cam["source"]))
        src_fps = cap.get(cv2.CAP_PROP_FPS) or 25
        frame_no, fails, n, t_fps = 0, 0, 0, time.time()
        while not self.stop_event.is_set():
            ok, frame = cap.read()
            if not ok:
                if is_file:                      # loop demo videos
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                fails += 1
                if fails > 30:
                    self.status, self.error = "offline", "Lost camera signal"
                    return
                time.sleep(0.05)
                continue
            fails = 0
            self.status = "online"
            self.last_raw = frame
            frame_no += 1
            now = time.time()

            fresh = frame_no % config.FRAME_SKIP == 0 or not self.last_dets and frame_no == 1
            if fresh:
                self.last_dets = self._infer(frame)
            annotated = self._annotate(frame, self.last_dets)
            if fresh:
                self._process_violations(self.last_dets, annotated, now, frame)
            self._push(annotated, now)

            n += 1
            if now - t_fps >= 2:
                self.fps = round(n / (now - t_fps), 1)
                n, t_fps = 0, now
            if is_file:
                time.sleep(max(0.0, 1.0 / src_fps - 0.01))

    # ------------------------------------------------------------------ detection
    def _infer(self, frame):
        kw = dict(conf=config.CONFIDENCE_THRESHOLD, imgsz=config.IMAGE_SIZE, verbose=False)
        res = None
        if self.use_track:
            try:
                res = self.model.track(frame, persist=True, tracker=config.TRACKER, **kw)[0]
            except Exception as e:
                print(f"[{self.cam['name']}] tracking unavailable ({e}); falling back to plain detection")
                self.use_track = False
        if res is None:
            res = self.model.predict(frame, **kw)[0]

        dets = []
        boxes = res.boxes
        ids = boxes.id.int().tolist() if getattr(boxes, "id", None) is not None else None
        for i, box in enumerate(boxes):
            name = str(self.model.names[int(box.cls[0])]).lower().strip().replace(" ", "_").replace("-", "_")
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            violation = config.is_violation_class(name) and (not self.required or name in self.required)
            dets.append({"name": name, "cid": int(box.cls[0]), "conf": float(box.conf[0]), "box": (x1, y1, x2, y2),
                         "tid": ids[i] if ids else None, "violation": violation})
        dets.extend(self._zone_intrusions(dets, frame.shape))
        return dets

    # ------------------------------------------------------------------ restricted zone
    def _zone_pixels(self, shape):
        h, w = shape[:2]
        return np.array([(int(x * w), int(y * h)) for x, y in self.zone], dtype=np.int32)

    def _zone_intrusions(self, dets, shape):
        """One extra 'restricted_zone' detection for every person whose feet (bottom-centre of the box) are
        inside the zone. It goes through the same confirmation / cooldown / alert path as a missing helmet."""
        if not self.zone:
            return []
        poly = self._zone_pixels(shape)
        found = []
        for d in dets:
            if d["name"] not in PERSON_NAMES:
                continue
            x1, y1, x2, y2 = d["box"]
            if cv2.pointPolygonTest(poly, (float((x1 + x2) / 2), float(y2)), False) >= 0:
                found.append({"name": ZONE_VIOLATION, "cid": d["cid"], "conf": d["conf"], "box": d["box"],
                              "tid": d["tid"], "violation": True, "zone": True})
        return found

    def _process_violations(self, dets, annotated, now, raw=None):
        seen = set()
        for d in dets:
            if not d["violation"]:
                continue
            key = (d["tid"] if d["tid"] is not None else -1, d["name"])
            if key in seen:
                continue
            seen.add(key)
            st = self.state.setdefault(key, {"first": now, "streak": 0, "reports": 0, "last_report": 0.0, "conf": 0.0})
            st["streak"] += 1
            st["last"] = now
            st["last_box"] = d["box"]
            st["conf"] = max(st["conf"], d["conf"])
            if d.get("zone"):
                st["no_auto"] = True           # the AI never closes a restricted-zone incident: a person decides
            st["gone_since"] = None            # it is back: cancel any pending AI decision
            st["ok_hits"] = st["miss_hits"] = 0
            if st["streak"] < config.REQUIRED_FRAMES:
                continue
            interval = 0 if st["reports"] == 0 else config.INCIDENT_COOLDOWN
            if now - st["last_report"] >= interval and now - self.class_last.get(d["name"], 0) >= config.MIN_GAP_SECONDS:
                self._report(key, st, annotated, now, raw, dets)
        for key in list(self.state):
            if key in seen:
                continue
            st = self.state[key]
            st["streak"] = 0
            if st.get("codes") and not st.get("closed"):
                self._check_resolved(st, key, dets, now)
            if (not st.get("codes") or st.get("closed")) and now - st.get("last", 0) > GRACE_SECONDS:
                del self.state[key]

    def _check_resolved(self, st, key, dets, now):
        """The violation is no longer seen. Look for the matching PPE at the same place for a few seconds:
        found -> the AI closes the incident. Not found -> a human decides."""
        if st.get("gone_since") is None:
            st["gone_since"] = now
            st["ok_hits"] = st["miss_hits"] = 0
        names = ai_review.counterparts(key[1])
        ok = any((not d["violation"]) and d["name"] in names and d["conf"] >= config.AUTO_RESOLVE_MIN_CONF
                 and ai_review.near(st["last_box"], d["box"]) for d in dets)
        st["ok_hits" if ok else "miss_hits"] += 1
        waited = now - st["gone_since"]
        if waited < config.AUTO_RESOLVE_CONFIRM_SECONDS:
            return
        st["closed"] = True
        if not config.AUTO_RESOLVE or st.get("no_auto"):
            return                              # switched off, or the AI was unsure from the start: stays for a human
        total = st["ok_hits"] + st["miss_hits"]
        ppe = key[1][3:] if key[1].startswith("no_") else key[1]
        if total >= 3 and st["ok_hits"] >= 0.7 * total:
            note = f"the camera now shows the worker wearing {ppe.replace('_', ' ')} (checked for {waited:.0f} seconds)."
            for code in st["codes"]:
                ai_review.auto_resolve(code, note)
        else:
            reason = (f"The {key[1].replace('_', ' ')} violation stopped, but the AI could not see the worker wearing "
                      f"{ppe.replace('_', ' ')}. The worker may have left the camera view.")
            for code in st["codes"]:
                ai_review.needs_review(code, reason)

    def _report(self, key, st, annotated, now, raw=None, dets=None):
        tid, name = key
        ongoing = st["reports"] > 0
        duration = now - st["first"]
        try:
            inc, code = db.insert_incident(name, st["conf"], None, camera=self.cam,
                                           track_id=None if tid == -1 else tid,
                                           duration_sec=duration, ongoing=ongoing)
            filename = f"{code}_{datetime.now():%Y%m%d_%H%M%S}.jpg"
            cv2.imwrite(os.path.join(config.INCIDENTS_DIR, filename), annotated)
            db.update_incident_fields(code, screenshot=filename)
            inc["screenshot"] = filename
            if name == ZONE_VIOLATION:
                self._describe_intrusion(inc, code)
        except Exception as e:
            print(f"[{self.cam['name']}] could not save incident: {e}")
            return
        if raw is not None and config.SAVE_TRAINING_FRAMES:
            self._save_training_frame(code, raw, dets or [])
        st.setdefault("codes", []).append(code)
        if st["conf"] < config.REVIEW_CONFIDENCE:
            st["no_auto"] = True                # never auto-close something the AI itself is unsure about
            ai_review.flag_low_confidence(code, st["conf"])
        st["reports"] += 1
        st["last_report"] = now
        self.class_last[name] = now
        print(f"INCIDENT {code} {name} ({st['conf']:.2f}) on {self.cam['name']}")

        pre = [j for t, j in self.buffer if t >= now - config.CLIP_PRE_SECONDS]
        self.pending.append({"code": code, "frames": pre, "end": now + config.CLIP_POST_SECONDS})
        bus.publish({"type": "incident", "incident": inc})
        alerts.dispatch(inc)

    def _describe_intrusion(self, inc, code):
        """Wording, severity (at least HIGH) and deadline for a restricted-zone incident."""
        fields = {}
        desc = str(inc.get("description") or "")
        if "restricted zone" not in desc.lower():
            tail = desc.split("Violation has continued", 1)
            desc = "A person was detected inside a restricted zone." + (
                " Violation has continued" + tail[1] if len(tail) > 1 else "")
            fields["description"] = desc
        sevs = incident_service.SEVERITIES
        if inc.get("severity") in sevs and sevs.index(inc["severity"]) < sevs.index("HIGH"):
            fields["severity"] = "HIGH"
            sla = getattr(config, "SLA_MINUTES", {}).get("HIGH", 120)
            fields["due_at"] = (datetime.now() + timedelta(minutes=int(sla))).strftime("%Y-%m-%d %H:%M:%S")
        if fields:
            db.update_incident_fields(code, **fields)
            inc.update(fields)

    def _save_training_frame(self, code, raw, dets):
        """Raw (un-annotated) frame + what the model predicted, in YOLO label format.
        If the incident is later marked 'False alarm', these are the files to correct and retrain on."""
        try:
            h, w = raw.shape[:2]
            cv2.imwrite(os.path.join(config.TRAINING_DIR, f"{code}.jpg"), raw, [cv2.IMWRITE_JPEG_QUALITY, 92])
            clamp = lambda v: min(max(v, 0.0), 1.0)
            lines = []
            for d in dets:
                if d.get("zone"):
                    continue                    # not a model prediction, just a rule: no label for it
                x1, y1, x2, y2 = d["box"]
                lines.append(f"{d['cid']} {clamp((x1 + x2) / 2 / w):.6f} {clamp((y1 + y2) / 2 / h):.6f} "
                             f"{clamp((x2 - x1) / w):.6f} {clamp((y2 - y1) / h):.6f}")
            with open(os.path.join(config.TRAINING_DIR, f"{code}.txt"), "w") as f:
                f.write("\n".join(lines))
        except Exception as e:
            print("could not save training frame:", e)

    # ------------------------------------------------------------------ drawing / streaming
    def _annotate(self, frame, dets):
        img = frame.copy()
        any_violation = False
        if self.zone:
            poly = self._zone_pixels(img.shape)
            tint = img.copy()
            cv2.fillPoly(tint, [poly], (0, 0, 255))
            img = cv2.addWeighted(tint, 0.18, img, 0.82, 0)
            cv2.polylines(img, [poly], True, (0, 0, 255), 2)
            cv2.putText(img, "RESTRICTED", (int(poly[:, 0].min()) + 6, int(poly[:, 1].min()) + 22),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)
        for d in dets:
            x1, y1, x2, y2 = d["box"]
            color = (0, 0, 255) if d["violation"] else (0, 200, 0)
            any_violation = any_violation or d["violation"]
            cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            label = f"{d['name']} {d['conf']:.2f}" + (f" #{d['tid']}" if d["tid"] is not None else "")
            cv2.putText(img, label, (x1, max(y1 - 8, 18)), cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2)
        status = "PPE VIOLATION" if any_violation else "SAFE"
        cv2.putText(img, f"{self.cam['name']}  |  {status}", (16, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8,
                    (0, 0, 255) if any_violation else (0, 200, 0), 2)
        cv2.putText(img, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), (16, img.shape[0] - 14),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1)
        return img

    def _push(self, img, now):
        h, w = img.shape[:2]
        view = cv2.resize(img, (960, int(h * 960 / w))) if w > 960 else img
        ok, buf = cv2.imencode(".jpg", view, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if ok:
            self.latest_jpeg = buf.tobytes()
            self.frame_id += 1
        if now - self.last_buf >= 0.1:           # ~10 fps for the clip buffer
            self.last_buf = now
            small = cv2.resize(img, (640, int(h * 640 / w))) if w > 640 else img
            ok2, sb = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 70])
            if ok2:
                jpg = sb.tobytes()
                self.buffer.append((now, jpg))
                while self.buffer and self.buffer[0][0] < now - config.CLIP_PRE_SECONDS - 1:
                    self.buffer.popleft()
                for p in list(self.pending):
                    p["frames"].append(jpg)
                    if now >= p["end"]:
                        self.pending.remove(p)
                        fn = f"{p['code']}.mp4"
                        threading.Thread(target=_write_clip, args=(p["frames"], fn, p["code"]), daemon=True).start()


class DetectorManager:
    def __init__(self):
        self.workers = {}
        self.lock = threading.Lock()

    def start_camera(self, cam):
        if config.DISABLE_DETECTION or not cam.get("enabled"):
            return
        with self.lock:
            self.stop_camera(cam["id"], _locked=True)
            w = CameraWorker(cam)
            self.workers[cam["id"]] = w
            w.start()

    def stop_camera(self, cam_id, _locked=False):
        w = self.workers.pop(cam_id, None)
        if w:
            w.stop()

    def restart_camera(self, cam_id):
        cam = db.get_camera(cam_id)
        self.stop_camera(cam_id)
        if cam:
            self.start_camera(cam)

    def start_all(self):
        for cam in db.list_cameras(only_enabled=True):
            self.start_camera(cam)

    def stop_all(self):
        for cid in list(self.workers):
            self.stop_camera(cid)

    def get(self, cam_id):
        return self.workers.get(cam_id)

    def info(self, cam):
        w = self.workers.get(cam["id"])
        if config.DISABLE_DETECTION:
            return {"status": "detection_disabled", "fps": 0, "error": ""}
        if not cam.get("enabled"):
            return {"status": "disabled", "fps": 0, "error": ""}
        if not w:
            return {"status": "stopped", "fps": 0, "error": ""}
        return {"status": w.status, "fps": w.fps, "error": w.error}


manager = DetectorManager()