"""A queue that runs export jobs one at a time on a background thread."""

import copy
import queue
import threading
import time
import uuid


class Cancelled(Exception):
    pass


class Jobs:
    """run_car(car_id, car_dir, options, log, progress) -> result dict, raises on failure.
    progress(fraction, message) raises Cancelled once the job is cancelled."""

    MAX_FINISHED = 30  # finished jobs kept for the UI; older ones are dropped

    def __init__(self, run_car, on_result=None):
        self.run_car = run_car
        self.on_result = on_result  # on_result(car_id, car_dir, options, result) after each car
        self.jobs = {}
        self.order = []
        self.lock = threading.Lock()
        self.queue = queue.Queue()
        threading.Thread(target=self._worker, daemon=True, name="kn5-jobs").start()

    def submit(self, cars, options=None):
        job = {"id": uuid.uuid4().hex[:12], "state": "queued", "total": len(cars), "done": 0,
               "current": None, "fraction": 0.0, "message": "waiting", "log": [], "results": [],
               "cancelled": False}
        with self.lock:
            self.jobs[job["id"]] = job
            self.order.append(job["id"])
        self.queue.put((job, cars, dict(options or {})))
        return job["id"]

    def get(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            return copy.deepcopy(job) if job else None

    def cancel(self, job_id):
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return False
            if job["state"] != "done":
                job["cancelled"] = True
            return True

    def _prune(self):
        with self.lock:
            finished = [i for i in self.order if self.jobs[i]["state"] == "done"]
            for i in finished[:max(0, len(finished) - self.MAX_FINISHED)]:
                del self.jobs[i]
                self.order.remove(i)

    def _worker(self):
        while True:
            job, cars, options = self.queue.get()
            try:
                self._run(job, cars, options)
            finally:
                job["state"] = "done"
                job["current"] = None
                job["message"] = "cancelled" if job["cancelled"] else "finished"
                self._prune()

    def _run(self, job, cars, options):
        job["state"] = "running"
        for car_id, car_dir in cars:
            result = {"id": car_id, "ok": False}
            if job["cancelled"]:
                result.update(error="cancelled", cancelled=True, seconds=0)
                self._finish(job, result)
                continue
            job["current"] = car_id
            start = time.time()

            def log(msg):
                with self.lock:
                    job["log"].append(msg)

            def progress(fraction, message):
                if job["cancelled"]:
                    raise Cancelled()
                job["fraction"] = (job["done"] + fraction) / job["total"]
                job["message"] = message

            log("%s" % car_id)
            try:
                result.update(self.run_car(car_id, car_dir, options, log, progress))
                result["ok"] = True
            except Cancelled:
                result.update(error="cancelled", cancelled=True)
                log("  - cancelled")
            except Exception as e:
                result["error"] = str(e) or e.__class__.__name__
                log("  ! failed: %s" % result["error"])
            result["seconds"] = round(time.time() - start, 1)
            self._finish(job, result)
            if self.on_result and not result.get("cancelled"):
                try:
                    self.on_result(car_id, car_dir, options, dict(result))
                except Exception:  # history is a convenience; never break an export over it
                    pass

    def _finish(self, job, result):
        with self.lock:
            job["results"].append(result)
            job["done"] += 1
            job["fraction"] = job["done"] / job["total"]
