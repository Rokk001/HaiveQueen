"""Build jobs: how many parallel build jobs a building agent gets, the RAM measurement of a build and the dashboard line.

Ports the build-job parts of lib.ps1 and build-sampler.ps1. The sampler is started by the agent process:
`python -m haivequeen.buildjobs sample --parent-pid N --out FILE` (one line `ISO time;free MB;compiler count` every 10 s until the parent is gone).
"""
import argparse
import math
import os
import re
import sys
import time
from datetime import datetime

from . import runtime
from . import platform as osplat

DEFAULT_ENV_VAR = "BUILD_JOBS"


def settings_from(value) -> dict:
    """Build-job settings with the defaults filled in, or None when there is no block.

    `value` is the buildJobs setting: None (no block), a number (0 = automatic with the defaults, N = fixed N jobs) or a table with
    envVar, coreShare ('2/3' or a number), reserveMB, perJobMB (number or 'auto'), firstJobs and fixed.
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        value = {"fixed": int(value)} if int(value) > 0 else {}
    share = 2.0 / 3.0
    if value.get("coreShare") is not None:
        cs = str(value["coreShare"])
        m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", cs)
        share = float(m.group(1)) / float(m.group(2)) if m else float(cs)
    per = value.get("perJobMB")
    per = float(per) if per is not None and str(per) != "auto" else None
    return {
        "EnvVar": str(value.get("envVar") or DEFAULT_ENV_VAR), "CoreShare": share,
        "ReserveMB": float(value["reserveMB"]) if value.get("reserveMB") is not None else 1024.0,
        "PerJobMB": per, "FirstJobs": int(value["firstJobs"]) if value.get("firstJobs") is not None else 2,
        "Fixed": int(value["fixed"]) if value.get("fixed") is not None else None,
    }


def job_count(bj: dict, cores: int, free_mb: float, measured_mb) -> dict:
    """Number of parallel build jobs: the smaller of (cores x coreShare, rounded up) and ((free RAM - reserve) / use per job, rounded down), at least 1.

    Without any use value (neither configured nor measured) firstJobs applies, never more than the core limit; `fixed` overrides everything.
    Returns {Jobs, Why, PerJobMB}.
    """
    core_limit = max(1, int(math.ceil(cores * bj["CoreShare"])))
    if bj["Fixed"] is not None:
        return {"Jobs": max(1, bj["Fixed"]), "Why": "fixed", "PerJobMB": None}
    per = bj["PerJobMB"]
    if per is None and measured_mb is not None and float(measured_mb) > 0:
        per = float(measured_mb)
    if per is None or per <= 0:
        return {"Jobs": max(1, min(bj["FirstJobs"], core_limit)), "Why": "first", "PerJobMB": None}
    ram_limit = int(math.floor((free_mb - bj["ReserveMB"]) / per))
    return {"Jobs": max(1, min(core_limit, ram_limit)), "Why": "ram" if ram_limit < core_limit else "cores", "PerJobMB": per}


def use_mb(start_free_mb: float, min_free_mb: float, jobs: int) -> float:
    """RAM use per job in MB: (free RAM at the start - lowest free RAM seen) / jobs set; never negative."""
    return float(round(max(0.0, start_free_mb - min_free_mb) / max(1, jobs)))


def stored_per_job(conn, project: str, host: str = None):
    """Highest use per job (MB) stored for this host and project, or None when never measured."""
    v = runtime.build_max_per_job(conn, project, host or runtime.host_name())
    return None if v is None else float(v)


def save_per_job(conn, project: str, use: float, jobs: int = 0, free_start: float = 0, free_min: float = 0,
                 duration_s: float = 0, host: str = None) -> float:
    """Store one build measurement row and return the highest value stored afterwards (the stored value only ever grows)."""
    runtime.build_add(conn, project, host or runtime.host_name(), jobs, free_start, free_min, use, duration_s)
    return stored_per_job(conn, project, host)


def read_samples_min(sample_file, start_free_mb: float) -> float:
    """Lowest free RAM (MB) in a sample file (`time;free MB;count` lines), at most the start value."""
    low = start_free_mb
    try:
        with open(sample_file, encoding="utf-8-sig") as h:
            for line in h:
                parts = line.strip().split(";")
                if len(parts) >= 2:
                    try:
                        low = min(low, float(parts[1]))
                    except ValueError:
                        continue
    except OSError:
        pass
    return low


def complete_measure(env, name: str, jobs: int, start_free_mb: float, sample_file, started: datetime, now: datetime = None) -> dict:
    """End the measurement of one build: lowest free RAM from the samples, use per job, stored maximum and one ROUNDS.log line.

    `env` is a ProjectEnv (connection, project name, log()). Returns {MinFreeMB, UseMB, StoredMB}.
    """
    low = read_samples_min(sample_file, start_free_mb)
    use = use_mb(start_free_mb, low, jobs)
    dur = (now or datetime.now()) - started
    secs = int(dur.total_seconds())
    stored = save_per_job(env.conn, env.name, use, jobs, start_free_mb, low, dur.total_seconds())
    env.log("build %s: jobs=%d, duration=%02d:%02d:%02d, lowest free RAM=%d MB, use per job=%d MB (stored maximum %d MB)" % (
        name, jobs, secs // 3600, secs // 60 % 60, secs % 60, low, use, stored))
    return {"MinFreeMB": low, "UseMB": use, "StoredMB": stored}


def plan_build(env, free_mb: float = None, cores: int = None) -> dict:
    """Job count the next build of this project gets: {Jobs, Why, PerJobMB, EnvVar, FreeMB}, or None when the project has no build-job block."""
    bj = settings_from(env.settings.get("buildJobs"))
    if bj is None:
        return None
    free = float(osplat.free_ram_mb() if free_mb is None else free_mb)
    calc = job_count(bj, cores or osplat.logical_cores(), free, stored_per_job(env.conn, env.name))
    calc.update({"Jobs": 1, "Why": "rule: one job", "EnvVar": bj["EnvVar"], "FreeMB": free})   # the owner's rule is --parallel 1, whatever cores and RAM allow
    return calc


def jobs_line(env, agents: list, compiler_count: int = None, cores: int = None, free_mb: float = None):
    """Build-jobs text of the dashboard system line (C10), or None without a build-job block.

    'Build jobs N of M cores (running|next build) | per job X MB | active: N cores'; the active part shows only while a build
    runs or compiler processes exist. `compiler_count` overrides the live count of the project's buildProcessNames (tests).
    """
    bj = settings_from(env.settings.get("buildJobs"))
    if bj is None:
        return None
    per = stored_per_job(env.conn, env.name)
    n_cores = cores or osplat.logical_cores()
    run = next((a for a in agents if a.get("status") == "running" and a.get("buildJobs")), None)
    if run:
        txt = "Build jobs %s of %d cores (running)" % (run["buildJobs"], n_cores)
    else:
        txt = "Build jobs 1 of %d cores (next build)" % n_cores   # the owner's rule: one job, the same value bzz integration acquire hands out
    txt += " | per job " + ("%.0f MB" % per if per is not None else "not measured yet")
    if compiler_count is None:
        compiler_count = osplat.count_processes(env.settings.get("buildProcessNames") or [])
    if run or compiler_count > 0:
        txt += " | active: %d cores" % compiler_count
    return txt


def sample_loop(out, parent_pid: int = 0, interval: float = 10.0, names=(), max_samples: int = 0) -> int:
    """Write `ISO time;free MB;compiler count` to the file every `interval` seconds until the parent process is gone; returns the number written."""
    written = 0
    while True:
        line = "%s;%d;%d" % (datetime.now().isoformat(timespec="seconds"), osplat.free_ram_mb(), osplat.count_processes(names))
        with open(out, "a", encoding="utf-8") as h:
            h.write(line + "\n")
        written += 1
        if (parent_pid and not osplat.process_alive(parent_pid)) or (max_samples and written >= max_samples):
            return written
        time.sleep(interval)


def main(argv=None) -> int:
    """Command line of the sampler: `sample --out FILE [--parent-pid N] [--names cl.exe,link.exe]`; BZZ_SAMPLE_SEC sets the interval."""
    ap = argparse.ArgumentParser(prog="haivequeen.buildjobs")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sample")
    p.add_argument("--out", required=True)
    p.add_argument("--parent-pid", type=int, default=0)
    p.add_argument("--names", default="")
    args = ap.parse_args(argv)
    sample_loop(args.out, args.parent_pid, float(os.environ.get("BZZ_SAMPLE_SEC", "10")),
                [n for n in args.names.split(",") if n])
    return 0


if __name__ == "__main__":
    sys.exit(main())
