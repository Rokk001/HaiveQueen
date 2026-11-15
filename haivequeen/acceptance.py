"""Evidence-scoped, asynchronous preparation of owner acceptance descriptions."""
import hashlib
import json
import re
from pathlib import Path

from . import db, roles, runtime


def evidence(conn, env, plan):
    from . import orchestrator, plans
    agents = [a for a in runtime.list_agents(conn, env.name, "all")
              if a.get("plan_path") == plan and a.get("package") != "OWNER-ACCEPTANCE"]
    reports = []
    for agent in agents:
        path = env.run_dir(agent["name"]) / "final.txt"
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = None
        reports.append({"package": agent.get("package"), "phase": agent.get("phase"),
                        "report": str(path), "digest": digest, "cwd": agent.get("cwd")})
    plan_file = Path(env.workspace) / plan
    try:
        # Parking for acceptance changes status, not the reviewed requirements.
        content = re.sub(r'(?m)^status\s*=.*$', '', plan_file.read_text(encoding="utf-8-sig"))
        plan_digest = hashlib.sha256(content.encode()).hexdigest()
    except OSError:
        plan_digest = None
    state_file = Path(plans.state_file_path(str(plan_file)))
    try:
        state_digest = hashlib.sha256(state_file.read_bytes()).hexdigest()
    except OSError:
        state_digest = None
    return {"plan_file": str(plan_file), "plan_digest": plan_digest,
            "state_file": str(state_file), "state_digest": state_digest,
            "submission": runtime.get_submission(conn, env.name, plan),
            "packages": runtime.list_packages(conn, env.name, plan),
            "decisions": [d for d in runtime.list_decisions(conn, env.name, active_only=True)
                          if d.get("plan_path") in (None, "", plan)],
            "answers": db.rows(conn, "SELECT id,item,answer,answered_by,owner_check_result FROM questions "
                               "WHERE project=? AND plan_path=? AND answer IS NOT NULL ORDER BY id", (env.name, plan)),
            "reports": reports, "integration": orchestrator.last_info(conn, env.name, plan)}


def validate(brief, sources):
    """Reject incomplete or untraceable descriptions before they reach the owner."""
    if not isinstance(brief, dict):
        raise ValueError("Expected a JSON object")
    packages = brief.get("packages", [])
    if not isinstance(packages, list) or any(not isinstance(p, dict) for p in packages):
        raise ValueError("Expected package objects")
    expected = {p["id"] for p in sources["packages"]}
    if {p.get("id") for p in packages} != expected or len(packages) != len(expected):
        raise ValueError("Cover every package exactly once")
    decisions = {d["id"] for d in sources["decisions"]}
    if set(brief.get("decisions_considered", [])) != decisions:
        raise ValueError("Account for every current decision")
    owner_ids = {a["id"] for a in sources["answers"] if a.get("answered_by") == "owner" and a.get("answer")}
    owner_decisions = {d["id"] for d in sources["decisions"] if d.get("source") == "owner"}
    for package in packages:
        if not package.get("summary") or package.get("state") not in ("pending", "accepted", "open", "technical"):
            raise ValueError("Each package needs a plain-language summary and acceptance state")
        if package["state"] != "open" and not package.get("evidence"):
            raise ValueError("Implemented work needs evidence")
        if package["state"] == "accepted":
            ids = set(package.get("acceptance_ids", []))
            decision_ids = set(package.get("acceptance_decisions", []))
            if not (ids or decision_ids) or not ids <= owner_ids or not decision_ids <= owner_decisions:
                raise ValueError("Acceptance needs an explicit owner answer")
        if package["state"] == "pending":
            steps = package.get("steps", [])
            if not steps or any(not s.get("action") or not s.get("expected") for s in steps):
                raise ValueError("Pending checks need actions and visible expected results")
    if any(not c.get("text") or not c.get("evidence") for c in brief.get("checks", [])):
        raise ValueError("Automatic check claims need evidence")
    for field in ("checks", "uncertainties", "technical_notes"):
        if not isinstance(brief.get(field), list):
            raise ValueError("Missing section: " + field)
    for missing in brief.get("open_requirements", []):
        if missing.get("package") not in expected or not missing.get("text"):
            raise ValueError("Open requirements need their package and description")


def render(brief, sources, language):
    german = language.lower() in ("german", "de", "deutsch")
    def label(en, de):
        return de if german else en
    info = sources["integration"]
    lines = [label("Plan acceptance", "Planabnahme")]
    if info.get("deployed"):
        lines += [label("HQ recorded deployment of commit: ", "HQ protokollierte Auslieferung von Commit: ") + str(info["deployed"])]
        lines += [label("Current installed version not independently verified.", "Aktuell installierte Version nicht unabhängig bestätigt.")]
    else:
        lines += [label("Delivery / installed version: not verified.", "Auslieferung / installierte Version: nicht nachgewiesen.")]
    if info.get("commit"):
        lines += [label("Integrated commit: ", "Integrierter Commit: ") + str(info["commit"])]
    for state, en, de in (("pending", "Your checks", "Deine Prüfschritte"),
                          ("accepted", "Already accepted — no repeat acceptance", "Bereits akzeptiert – keine erneute Abnahme"),
                          ("technical", "Technical packages already verified", "Bereits technisch geprüfte Pakete"),
                          ("open", "Open / not proven delivered", "Offen / nicht nachweisbar ausgeliefert")):
        lines += ["", label(en, de)]
        items = [p for p in brief["packages"] if p["state"] == state]
        if not items:
            lines.append(label("None reported.", "Keine gemeldet."))
        for p in items:
            lines.append("[%s] %s" % (p["id"], p["summary"]))
            for n, step in enumerate(p.get("steps", []) if state == "pending" else [], 1):
                lines.append("%d. %s → %s" % (n, step["action"], step["expected"]))
        if state == "open":
            lines += ["[%s] %s" % (p["package"], p["text"]) for p in brief.get("open_requirements", [])]
    lines += ["", label("Automatically verified technical checks", "Automatisch geprüfte technische Punkte")]
    lines += [c["text"] + " (" + c["evidence"] + ")" for c in brief["checks"]] or [label("No successful checks evidenced.", "Keine erfolgreichen Prüfungen belegt.")]
    if brief["uncertainties"]:
        lines += ["", label("Unresolved evidence", "Ungeklärte Nachweise"), *brief["uncertainties"]]
    if brief["technical_notes"]:
        lines += ["", label("Technical addendum", "Technische Zusatzinformation"), *brief["technical_notes"]]
    lines += ["", label("Report a failed check with its package ID; previously accepted points stay accepted.",
                        "Nenne bei einem Fehler die Paket-ID und den Prüfschritt. Bereits akzeptierte Punkte bleiben akzeptiert.")]
    return "\n".join(lines)


def prepare(conn, env, plan):
    """Return a validated cached description, or schedule one review without blocking a round."""
    from . import agent_process, readygate, scheduler
    sources = evidence(conn, env, plan)
    scope = hashlib.sha256(json.dumps(sources, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    key = "acceptance-review:" + plan
    saved = json.loads(runtime.kv_get(conn, env.name, key) or "{}")
    if saved.get("scope") != scope:
        saved = {"scope": scope}
    if saved.get("brief"):
        validate(saved["brief"], sources)
        return render(saved["brief"], sources, roles.language(env.settings))
    if saved.get("agent"):
        row = runtime.get_agent(conn, env.name, saved["agent"])
        if row is None or row.get("status") in scheduler.ACTIVE or agent_process.is_live(row):
            return None
        try:
            text = readygate.answer_text(env.run_dir(saved["agent"])).strip()
            if text.startswith("```"):
                text = "\n".join(text.splitlines()[1:-1])
            brief = json.loads(text)
            validate(brief, sources)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            saved["error"] = str(exc)
            # One corrective pass, with the validator's precise finding; never unchanged retries.
            saved.pop("agent")
            saved["failed_attempts"] = saved.get("failed_attempts", 0) + 1
        else:
            saved["brief"] = brief
            runtime.kv_set(conn, env.name, key, json.dumps(saved, ensure_ascii=False))
            return render(brief, sources, roles.language(env.settings))
    if saved.get("failed_attempts", 0) >= 2:
        from . import faults
        if not saved.get("fault_reported"):
            faults.file_fault(conn, env.name, "acceptance_review", "Acceptance description for %s could not be validated: %s. "
                              "Review %s and its evidence before retrying; do not request product acceptance of an incomplete description."
                              % (plan, saved.get("error", "missing result"), key))
            saved["fault_reported"] = True
        runtime.kv_set(conn, env.name, key, json.dumps(saved, ensure_ascii=False))
        return None
    input_key = "acceptance-input:" + plan
    runtime.kv_set(conn, env.name, input_key, json.dumps(sources, ensure_ascii=False))
    prompt = roles.role_text("acceptance", {"project": env.name, "input_key": input_key,
                                            "language": roles.language(env.settings), "finding": saved.get("error", "")})
    name, reason = readygate._launch_check_agent(env, {"name": "acceptance"},
        {"plan": plan, "cwd": str(env.code_repo), "prompt": prompt, "package": "OWNER-ACCEPTANCE"})
    if name:
        saved["agent"] = name
    else:
        saved["waiting"] = reason
    runtime.kv_set(conn, env.name, key, json.dumps(saved, ensure_ascii=False))
    return None
