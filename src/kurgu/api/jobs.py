"""Arka plan iş kuyruğu.

Tek işçi iş parçacığı: ağır modeller (Whisper, görsel üretim) aynı anda yalnızca bir işte
çalışır ve bellek taşmaz. Arayüz (submit/get/events) soyut tutuldu; SaaS'a geçerken
Redis tabanlı bir kuyrukla (ör. Huey/RQ) değiştirilebilir.
"""

from __future__ import annotations

import queue
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable


@dataclass
class Job:
    id: str
    kind: str
    project: str | None
    status: str = "queued"  # queued | running | done | failed
    stage: str = ""
    progress: float = 0.0
    message: str = ""
    result: Any = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    events: list[dict] = field(default_factory=list)

    def public(self) -> dict:
        d = asdict(self)
        d.pop("events")
        return d


class JobManager:
    def __init__(self):
        self.jobs: dict[str, Job] = {}
        self.q: "queue.Queue[tuple[str, Callable]]" = queue.Queue()
        self.cond = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, kind: str, fn: Callable[[Callable[[str, float, str], None]], Any], project: str | None = None) -> Job:
        job = Job(id=uuid.uuid4().hex[:10], kind=kind, project=project)
        self.jobs[job.id] = job
        self.q.put((job.id, fn))
        self._emit(job, {"type": "queued"})
        return job

    def get(self, jid: str) -> Job | None:
        return self.jobs.get(jid)

    def active_for(self, project: str) -> list[Job]:
        return [j for j in self.jobs.values() if j.project == project and j.status in ("queued", "running")]

    def _emit(self, job: Job, ev: dict):
        with self.cond:
            ev = {**ev, "t": time.time(), "status": job.status, "stage": job.stage,
                  "progress": round(job.progress, 4), "message": job.message}
            job.events.append(ev)
            if len(job.events) > 500:
                job.events = job.events[-300:]
            self.cond.notify_all()

    def wait_events(self, job: Job, since: int, timeout: float = 15.0) -> list[dict]:
        with self.cond:
            if len(job.events) <= since and job.status not in ("done", "failed"):
                self.cond.wait(timeout)
            return job.events[since:]

    def _loop(self):
        while True:
            jid, fn = self.q.get()
            job = self.jobs[jid]
            job.status = "running"
            self._emit(job, {"type": "start"})
            last = [0.0]

            def progress(stage: str, frac: float, msg: str = ""):
                job.stage, job.progress = stage, max(0.0, min(frac, 1.0))
                if msg:
                    job.message = msg
                now = time.time()
                if now - last[0] > 0.25 or frac >= 1.0:  # olay yağmurunu sınırla
                    last[0] = now
                    self._emit(job, {"type": "progress"})

            try:
                job.result = fn(progress)
                job.status = "done"
            except Exception as e:
                job.status = "failed"
                job.error = f"{e}"
                job.message = str(e)[:300]
                traceback.print_exc()
            job.finished = time.time()
            self._emit(job, {"type": job.status, "result": job.result if job.status == "done" else None,
                             "error": job.error})


jobs = JobManager()
