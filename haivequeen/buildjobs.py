"""Build jobs: how many parallel build jobs a building agent gets, the RAM measurement of a build and the dashboard line.

Ports the build-job parts of lib.ps1 and build-sampler.ps1. The sampler is started by the agent process:
`python -m haivequeen.buildjobs sample --parent-pid N --out FILE` (one line `ISO time;free MB;build process count;build process RAM MB`
every 10 s until the parent is gone).
"""
import argparse
import json
import math
import os
import re
import sys
import time
from datetime import datetime

from . import runtime
from . import platform as osplat
from .i18n import tr

DEFAULT_ENV_VAR = "BUILD_JOBS"                 # name of the job count variable; a configured envVar (e.g. BUILD_MP_JOBS) wins
DEFAULT_PARALLEL_VAR = "BUILD_PARALLEL"        # name of the variable that carries the parallel value of the build command (--parallel)
DEFAULT_MIN_RAM_VAR = "BUILD_MIN_FREE_RAM_GB"  # name of the variable that carries the minimum free RAM before a build, in GB
DEFAULT_CORE_SHARE = 2.0 / 3.0                 # default core count: two thirds of the logical cores, rounded up
DEFAULT_MEASUREMENTS = 10                      # how many of the last measurements the median is taken from
DEFAULT_MIN_FREE_GB = 2.0                      # minimum free RAM before a build is handed out


def default_cores(cores: int) -> int:
    """The default core count for builds: two thirds of the logical cores, rounded up (8 cores: 6), at least 1."""
    return max(1, int(math.ceil(max(1, int(cores)) * DEFAULT_CORE_SHARE - 1e-9)))


def core_choices(cores: int) -> list:
    """The core counts the settings window offers: every whole number from 1 to the number of logical cores."""
    return list(range(1, max(1, int(cores)) + 1))


def settings_from(value) -> dict:
    """Build settings with the defaults filled in, or None when there is no block.

    `value` is the buildJobs setting of config.toml: None (no block), a number (0 = the defaults; N above 0 = N cores, the old form) or a table
    with `cores` (the core count of the settings window), `parallel`, `minFreeRamGB`, `envVar`, `parallelEnvVar`, `minFreeRamEnvVar`, `reserveMB`,
    `perJobMB` (number or 'auto'), `firstJobs`, `measurements` and the old keys `fixed` (read as core count) and `coreShare` ('2/3' or a number).
    The core count is `cores`, else `fixed`, else the old number; without any of them it follows `coreShare` (default two thirds, rounded up).
    """
    if value is None:
        return None
    if not isinstance(value, dict):
        value = {"cores": int(value)} if int(value) > 0 else {}
    share = DEFAULT_CORE_SHARE
    if value.get("coreShare") is not None:
        cs = str(value["coreShare"])
        m = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", cs)
        share = float(m.group(1)) / float(m.group(2)) if m else float(cs)
    per = value.get("perJobMB")
    per = float(per) if per is not None and str(per) != "auto" else None
    chosen = next((int(value[k]) for k in ("cores", "fixed") if value.get(k) is not None and int(value[k]) > 0), None)
    return {
        "EnvVar": str(value.get("envVar") or DEFAULT_ENV_VAR), "CoreShare": share, "Cores": chosen,
        "ParallelVar": str(value.get("parallelEnvVar") or DEFAULT_PARALLEL_VAR),
        "MinRamVar": str(value.get("minFreeRamEnvVar") or DEFAULT_MIN_RAM_VAR),
        "Parallel": max(1, int(value["parallel"])) if value.get("parallel") is not None else 1,
        "MinFreeGB": float(value["minFreeRamGB"]) if value.get("minFreeRamGB") is not None else DEFAULT_MIN_FREE_GB,
        "ReserveMB": float(value["reserveMB"]) if value.get("reserveMB") is not None else 1024.0,
        "PerJobMB": per, "FirstJobs": int(value["firstJobs"]) if value.get("firstJobs") is not None else 2,
        "Measurements": max(1, int(value["measurements"])) if value.get("measurements") is not None else DEFAULT_MEASUREMENTS,
    }


def core_limit(bj: dict, cores: int) -> int:
    """The number of cores builds may use on this machine: the chosen core count (never above the cores of the machine), without one two thirds
    (coreShare) of the cores rounded up; at least 1."""
    cores = max(1, int(cores))
    if bj.get("Cores"):
        return max(1, min(int(bj["Cores"]), cores))
    return max(1, min(cores, int(math.ceil(cores * bj["CoreShare"] - 1e-9))))


def job_count(bj: dict, cores: int, free_mb: float, measured_mb, concurrent: int = 0) -> dict:
    """Number of parallel build jobs per target: the smaller of the core limit and the RAM limit, at least 1.

    The core limit is the chosen core count (core_limit), divided by the number of builds that run at the same time (`concurrent` others plus this
    one) and by the parallel value N of the build command (floor, at least 1). The RAM limit is (free RAM - reserve) / (use per job x N), rounded down.
    Without any use value (neither configured nor a valid measurement) firstJobs applies, never more than the core limit.
    Returns {Jobs, Why, PerJobMB, Parallel}.
    """
    parallel = max(1, int(bj.get("Parallel") or 1))
    budget = max(1, core_limit(bj, cores) // (max(0, int(concurrent)) + 1))
    core_jobs = max(1, budget // parallel)
    per = bj["PerJobMB"]
    if per is None and measured_mb is not None and float(measured_mb) > 0:
        per = float(measured_mb)
    if per is None or per <= 0:
        return {"Jobs": max(1, min(bj["FirstJobs"], core_jobs)), "Why": "first", "PerJobMB": None, "Parallel": parallel}
    ram_limit = int(math.floor((free_mb - bj["ReserveMB"]) / (per * parallel)))
    return {"Jobs": max(1, min(core_jobs, ram_limit)), "Why": "ram" if ram_limit < core_jobs else "cores", "PerJobMB": per, "Parallel": parallel}


SAMPLE_SEC = 1.0   # sample interval of the build processes: compile processes live only seconds, a 10 s sampler misses most of them


def use_mb(peak_mb: float, jobs: int, parallel: int = 1, seen: int = 0) -> float:
    """RAM use per job in MB: the highest RAM sum of the build processes divided by the jobs the build got and the parallel value; never negative.

    `seen` is the highest number of build processes alive at one sample. When fewer than `jobs` were alive at the peak, the peak is shared by
    those processes only (dividing by all jobs would report far too little); more than `jobs` processes never raise the divisor."""
    divisor = min(int(jobs), int(seen)) if int(seen) > 0 else int(jobs)
    return float(round(max(0.0, float(peak_mb)) / max(1, divisor) / max(1, int(parallel))))


def median(values):
    """Median of a list of numbers, or None for an empty list."""
    v = sorted(float(x) for x in values)
    if not v:
        return None
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2.0


def stored_per_job(conn, project: str, host: str = None, count: int = DEFAULT_MEASUREMENTS):
    """Median RAM use per job (MB) of the last `count` valid measurements of this host and project (fewer when fewer exist), or None.

    Measurements stored before the RAM of the build processes was measured are not used."""
    return median(runtime.build_recent_per_job(conn, project, host or runtime.host_name(), count))


def save_per_job(conn, project: str, use: float, jobs: int = 0, free_start: float = 0, free_min: float = 0,
                 duration_s: float = 0, host: str = None, peak_mb: float = None, parallel: int = 1, count: int = DEFAULT_MEASUREMENTS):
    """Store one build measurement row and return the median of the last measurements afterwards."""
    runtime.build_add(conn, project, host or runtime.host_name(), jobs, free_start, free_min, use, duration_s,
                      peak_mb=peak_mb if peak_mb is not None else use * max(1, jobs) * max(1, parallel), parallel=parallel)
    return stored_per_job(conn, project, host, count)


def read_samples(sample_file) -> dict:
    """Read a sample file (`time;free MB;build process count;build process RAM MB` lines): {FreeMin, PeakMB, Seen}.

    FreeMin is the lowest free RAM seen (None without samples), PeakMB the highest RAM sum of the build processes and Seen the highest number of
    build processes seen at one time (0 = no build process was seen). Lines of the old format have no RAM column and count as no build process."""
    out = {"FreeMin": None, "PeakMB": 0.0, "Seen": 0}
    try:
        with open(sample_file, encoding="utf-8-sig") as h:
            for line in h:
                parts = line.strip().split(";")
                try:
                    if len(parts) >= 2:
                        free = float(parts[1])
                        out["FreeMin"] = free if out["FreeMin"] is None else min(out["FreeMin"], free)
                    if len(parts) >= 4:
                        out["PeakMB"] = max(out["PeakMB"], float(parts[3]))
                        out["Seen"] = max(out["Seen"], int(float(parts[2])))
                except ValueError:
                    continue
    except OSError:
        pass
    return out


def sample_line(names, root_pid) -> str:
    """One sample line `ISO time;free MB;number of build processes of the tree of root_pid;their RAM in MB` (without newline)."""
    ram, count = osplat.process_tree_ram(root_pid, names)
    return "%s;%d;%d;%d" % (datetime.now().isoformat(timespec="seconds"), osplat.free_ram_mb(), count, round(ram))


def complete_measure(env, name: str, jobs: int, start_free_mb: float, sample_file, started: datetime, now: datetime = None, parallel: int = 1,
                     count: int = DEFAULT_MEASUREMENTS) -> dict:
    """End the measurement of one build: the highest RAM sum of the build processes from the samples, use per job, stored median and one ROUNDS.log line.

    `env` is a ProjectEnv (connection, project name, log()). A build in which no build process was seen stores nothing.
    Returns {MinFreeMB, UseMB, StoredMB, PeakMB}; UseMB and StoredMB are None when nothing was stored.
    """
    smp = read_samples(sample_file)
    low = start_free_mb if smp["FreeMin"] is None else min(start_free_mb, smp["FreeMin"])
    dur = (now or datetime.now()) - started
    secs = int(dur.total_seconds())
    head = "build %s: jobs=%d, duration=%02d:%02d:%02d" % (name, jobs, secs // 3600, secs // 60 % 60, secs % 60)
    if smp["Seen"] <= 0 or smp["PeakMB"] <= 0:
        env.log("%s, no build process seen, nothing stored" % head)
        return {"MinFreeMB": low, "UseMB": None, "StoredMB": None, "PeakMB": 0.0}
    use = use_mb(smp["PeakMB"], jobs, parallel, smp["Seen"])
    stored = save_per_job(env.conn, env.name, use, jobs, start_free_mb, low, dur.total_seconds(), peak_mb=smp["PeakMB"], parallel=parallel, count=count)
    env.log("%s, build process RAM peak=%d MB, use per job=%d MB (median %d MB)" % (head, smp["PeakMB"], use, stored))
    return {"MinFreeMB": low, "UseMB": use, "StoredMB": stored, "PeakMB": smp["PeakMB"]}


def running_builds(conn, exclude: str = None) -> list:
    """Agents that run a build now (status running and a job count in their row), without the agent named `exclude`: [{name, jobs}]."""
    found = []
    for r in conn.execute("SELECT name, extra_json FROM agents WHERE status='running' AND archived=0"):
        try:
            extra = json.loads(r[1] or "{}")
        except ValueError:
            continue
        if extra.get("buildJobs") and r[0] != exclude:
            found.append({"name": r[0], "jobs": int(extra["buildJobs"])})
    return found


def _running_checks() -> int:
    """The compile checks that run now: they share the cores of the builds."""
    from . import integration
    return integration.running_checks()


def waiting_for_ram(bj: dict, free_mb: float):
    """The text why a build must wait (free RAM below the minimum free RAM before a build), or None when it may start."""
    if bj is None:
        return None
    if float(free_mb) < float(bj["MinFreeGB"]) * 1024.0:
        return "free RAM %.2f GB is below the minimum free RAM before a build of %g GB" % (float(free_mb) / 1024.0, bj["MinFreeGB"])
    return None


def build_environment(bj: dict, jobs: int) -> dict:
    """The environment variables a building agent gets: the job count, the parallel value and the minimum free RAM in GB (names from the settings)."""
    return {bj["EnvVar"]: str(int(jobs)), bj["ParallelVar"]: str(int(bj["Parallel"])), bj["MinRamVar"]: "%g" % bj["MinFreeGB"]}


def rule_text(value) -> str:
    """The build instructions for an agent (value = the buildJobs setting): which variables hold the job count, the parallel value and the minimum
    free RAM, and that they win over a project rule. Empty text when there is no build block."""
    bj = settings_from(None if isinstance(value, bool) else value)
    if bj is None:
        return ""
    return ("Build settings of the owner: the number of parallel build jobs is in the environment variable %s (for the plan build and the "
            "integration build use the count that bzz integration acquire returns), the parallel value is the number after --parallel of the "
            "build command and is in %s, and a build starts only while at least %g GB RAM is free (%s). These values win over a fixed "
            "--parallel value or a minimum RAM in a project rule; do not ask a question about that difference."
            % (bj["EnvVar"], bj["ParallelVar"], bj["MinFreeGB"], bj["MinRamVar"]))


def plan_build(env, free_mb: float = None, cores: int = None, exclude: str = None, host: str = None) -> dict:
    """Job count the next build of this project gets, computed now: {Jobs, Why, PerJobMB, Parallel, EnvVar, ParallelVar, MinRamVar, MinFreeGB,
    FreeMB, Environment}, or None when the project has no build block. Builds that run now share the chosen cores (`exclude` = the asking agent)."""
    bj = settings_from(env.settings.get("buildJobs"))
    if bj is None:
        return None
    free = float(osplat.free_ram_mb() if free_mb is None else free_mb)
    calc = job_count(bj, cores or osplat.logical_cores(), free, stored_per_job(env.conn, env.name, host, bj["Measurements"]),
                     concurrent=len(running_builds(env.conn, exclude)) + _running_checks())
    calc.update({"EnvVar": bj["EnvVar"], "ParallelVar": bj["ParallelVar"], "MinRamVar": bj["MinRamVar"], "MinFreeGB": bj["MinFreeGB"],
                 "FreeMB": free, "Environment": build_environment(bj, calc["Jobs"])})
    return calc


def jobs_line(env, agents: list, compiler_count: int = None, cores: int = None, free_mb: float = None, host: str = None):
    """Build-jobs text of the dashboard system line (C10), or None without a build block.

    'Build jobs N of M cores (running|next build) | per job X MB | active: N cores': N is the running job count during a build, else the count
    bzz integration acquire would hand out now; X is the median per job of the project. The active part shows only while a build runs or build
    processes exist. `compiler_count` overrides the live count of the project's buildProcessNames (tests).
    """
    bj = settings_from(env.settings.get("buildJobs"))
    if bj is None:
        return None
    n_cores = cores or osplat.logical_cores()
    per = stored_per_job(env.conn, env.name, host, bj["Measurements"])
    run = next((a for a in agents if a.get("status") == "running" and a.get("buildJobs")), None)
    if run:
        txt = tr("Build jobs %s of %d cores (running)", run["buildJobs"], n_cores)
    else:
        txt = tr("Build jobs %d of %d cores (next build)", plan_build(env, free_mb=free_mb, cores=n_cores, host=host)["Jobs"], n_cores)
    txt += tr(" | per job %s", "%.0f MB" % per if per is not None else tr("not measured yet"))
    if compiler_count is None:
        compiler_count = osplat.count_processes(env.settings.get("buildProcessNames") or [])
    if run or compiler_count > 0:
        txt += tr(" | active: %d cores", compiler_count)
    return txt


def sample_loop(out, parent_pid: int = 0, interval: float = SAMPLE_SEC, names=(), max_samples: int = 0) -> int:
    """Write `ISO time;free MB;build process count;build process RAM MB` to the file every `interval` seconds until the parent process is gone;
    returns the number written. The build processes are those below the parent process."""
    written = 0
    while True:
        with open(out, "a", encoding="utf-8") as h:
            h.write(sample_line(names, parent_pid) + "\n")
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
    sample_loop(args.out, args.parent_pid, float(os.environ.get("BZZ_SAMPLE_SEC", str(SAMPLE_SEC))),
                [n for n in args.names.split(",") if n])
    return 0


if __name__ == "__main__":
    sys.exit(main())
