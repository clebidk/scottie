"""Named pipelines as YAML.

A workflow file lists the pipeline stages to run, in order. The runner reads it
and calls the same functions `harness run` calls (harness/pipeline.py's STAGES
registry), so `harness workflow run ad-to-pages --input ...` produces exactly
what `harness run ...` produces. The YAML owns the ORDER; the code owns the
BEHAVIOUR. Nothing about a stage lives in the YAML.
"""
from pathlib import Path

import yaml

WORKFLOWS_DIR = Path(__file__).resolve().parent.parent / "workflows"


def list_workflows():
    if not WORKFLOWS_DIR.is_dir():
        return []
    return sorted(p.stem for p in WORKFLOWS_DIR.glob("*.yaml"))


def load_workflow(name):
    path = WORKFLOWS_DIR / f"{name}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"no workflow {name!r}; available: {list_workflows()}")
    return yaml.safe_load(path.read_text()) or {}


def stage_names(workflow):
    """The pipeline stages a workflow runs, in order.

    A step keyed `stage:` is a real pipeline stage and is executed. A step keyed
    `action:` describes something the workflow does around the pipeline and is
    NOT executed by this runner -- a workflow made only of actions is a
    specification, and `harness workflow run` on it exits 1 rather than
    pretending to have run it."""
    return [s["stage"] for s in (workflow.get("steps") or []) if isinstance(s, dict) and "stage" in s]
