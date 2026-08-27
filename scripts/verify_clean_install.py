"""Install the built wheel into a fresh venv and load the package and Streamlit app."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1].resolve()
TEMP_ROOT = (ROOT / "tmp").resolve()
TARGET = (TEMP_ROOT / "clean-install-venv").resolve()


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _run(command: list[str], *, cwd: Path = ROOT) -> None:
    subprocess.run(command, cwd=cwd, check=True)


def verify(*, keep: bool) -> dict[str, object]:
    if not _inside(TEMP_ROOT, ROOT) or not _inside(TARGET, TEMP_ROOT):
        raise RuntimeError("Refusing to use a clean-install directory outside project tmp")
    wheels = sorted((ROOT / "dist").glob("hy3_api_review_evaluator-*.whl"))
    if not wheels:
        raise RuntimeError("Build a wheel first with: python -m build")
    wheel = wheels[-1]
    if TARGET.exists():
        shutil.rmtree(TARGET)
    TEMP_ROOT.mkdir(parents=True, exist_ok=True)
    try:
        _run([sys.executable, "-m", "venv", str(TARGET)])
        python = TARGET / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        _run([str(python), "-m", "pip", "install", str(wheel)])
        _run([str(python), "-m", "pip", "check"])
        smoke = (
            "import sys; from pathlib import Path; "
            "import hy3_api_review_evaluator as package; "
            "assert Path(package.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
            "from hy3_api_review_evaluator.rubric import load_rubric; "
            "from hy3_api_review_evaluator.config import Settings; "
            "from hy3_api_review_evaluator.human_agreement import paired_metrics; "
            "r=load_rubric(); s=Settings.from_env(env_file=Path('__missing__.env')); "
            "assert len(r['dimensions'])==6; assert s.model=='hy3'; "
            "assert paired_metrics([100, 0], [100, 0])['mae']==0; "
            "import streamlit, pandas, openai; print('clean-import-ok')"
        )
        _run([str(python), "-c", smoke], cwd=TARGET)
        cli = TARGET / (
            "Scripts/hy3-evaluate.exe" if sys.platform == "win32" else "bin/hy3-evaluate"
        )
        _run([str(cli), "check"], cwd=TARGET)
        app_file = repr(str(ROOT / "app.py"))
        app_smoke = (
            "from streamlit.testing.v1 import AppTest; "
            f"app=AppTest.from_file({app_file}).run(timeout=20); "
            "assert not app.exception; "
            "assert app.title[0].value=='Hy3 API Review Evaluator'; "
            "print('clean-streamlit-ok')"
        )
        _run([str(python), "-c", app_smoke], cwd=TARGET)
        annotation_file = repr(str(ROOT / "annotation_app.py"))
        annotation_smoke = (
            "from streamlit.testing.v1 import AppTest; "
            f"app=AppTest.from_file({annotation_file}).run(timeout=20); "
            "assert not app.exception; assert len(app.title)==1; "
            "assert len(app.text_input)==1; print('clean-annotation-app-ok')"
        )
        _run([str(python), "-c", annotation_smoke], cwd=TARGET)
        for script in ("validate_dataset.py", "validate_results.py"):
            _run([str(python), str(ROOT / "scripts" / script)], cwd=TARGET)
        _run(
            [str(python), str(ROOT / "evaluation/run_human_agreement.py"), "--check"],
            cwd=TARGET,
        )
        return {
            "ok": True,
            "wheel": wheel.name,
            "package_import": True,
            "rubric_loaded": True,
            "cli_loaded": True,
            "streamlit_loaded": True,
            "annotation_app_loaded": True,
            "dependencies_consistent": True,
            "installed_wheel_used": True,
            "dataset_and_results_validated": True,
            "human_agreement_reproduced": True,
            "new_hy3_calls": 0,
        }
    finally:
        if TARGET.exists() and not keep:
            shutil.rmtree(TARGET)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--keep", action="store_true", help="Keep the temporary venv for debugging")
    return parser


if __name__ == "__main__":
    args = _parser().parse_args()
    print(json.dumps(verify(keep=args.keep), ensure_ascii=False, indent=2))
