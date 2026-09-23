"""Cross-platform local commands; Python 3.12+ and Docker Compose required."""
import os
import secrets
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
PYTHON = BACKEND / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
NPM = "npm.cmd" if os.name == "nt" else "npm"


def run(*args, cwd=ROOT):
    subprocess.run([str(a) for a in args], cwd=cwd, check=True)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "help"
    if action == "setup":
        env = ROOT / ".env"
        if not env.exists() or "replace-via-setup" in env.read_text():
            admin, runtime = secrets.token_hex(24), secrets.token_hex(24)
            content = (ROOT / ".env.example").read_text()
            content = content.replace("POSTGRES_ADMIN_PASSWORD=replace-via-setup", "POSTGRES_ADMIN_PASSWORD=" + admin)
            content = content.replace("POSTGRES_RUNTIME_PASSWORD=replace-via-setup", "POSTGRES_RUNTIME_PASSWORD=" + runtime)
            content = content.replace("mediaos_admin:replace-via-setup", "mediaos_admin:" + admin)
            content = content.replace("mediaos_runtime:replace-via-setup", "mediaos_runtime:" + runtime)
            if os.name != "nt":
                content += f"\nLOCAL_UID={os.getuid()}\nLOCAL_GID={os.getgid()}\n"
            env.write_text(content)
            if os.name != "nt":
                env.chmod(0o600)
        (ROOT / ".local").mkdir(exist_ok=True)
        (ROOT / ".local/renders").mkdir(mode=0o700, exist_ok=True)
        (ROOT / ".local/media").mkdir(mode=0o700, exist_ok=True)
        (ROOT / ".local/social").mkdir(mode=0o700, exist_ok=True)
        run(sys.executable, "-m", "venv", BACKEND / ".venv")
        run(PYTHON, "-m", "pip", "install", "-r", BACKEND / "requirements.lock")
        run(NPM, "ci", cwd=ROOT / "apps/console")
    elif action == "up":
        run("docker", "compose", "up", "-d", "postgres")
    elif action == "migrate":
        run(PYTHON, "-m", "app.admin", "migrate", cwd=BACKEND)
    elif action == "seed":
        run(PYTHON, "-m", "app.seed", cwd=BACKEND)
    elif action == "test":
        run(PYTHON, "-m", "app.admin", "create-test-db", cwd=BACKEND)
        run(PYTHON, "-m", "pytest", "-q", cwd=BACKEND)
    elif action == "check":
        run(PYTHON, "-m", "ruff", "check", "app", "tests", "migrations", cwd=BACKEND)
        run(PYTHON, "-m", "ruff", "format", "--check", "app", "tests", "migrations", cwd=BACKEND)
        run(PYTHON, "-m", "mypy", "app", cwd=BACKEND)
        run(PYTHON, "-m", "ruff", "check", "--config", BACKEND / "pyproject.toml", "scripts/deployment.py")
        run(PYTHON, "-m", "ruff", "format", "--check", "--config", BACKEND / "pyproject.toml", "scripts/deployment.py")
        run(NPM, "test", cwd=ROOT / "apps/console")
        run(NPM, "run", "build", cwd=ROOT / "apps/console")
    elif action == "dev":
        run("docker", "compose", "up", "-d", "--build", "api", "console")
    elif action == "acceptance":
        run(PYTHON, "-m", "app.acceptance", cwd=BACKEND)
    elif action == "visual-acceptance":
        run(PYTHON, "-m", "app.rendering.acceptance", cwd=BACKEND)
    elif action == "delivery-acceptance":
        run(PYTHON, "-m", "app.delivery.acceptance", cwd=BACKEND)
    elif action == "metrics-acceptance":
        run(PYTHON, "-m", "app.metrics.acceptance", cwd=BACKEND)
    elif action == "community-acceptance":
        run(PYTHON, "-m", "app.community.acceptance", cwd=BACKEND)
    elif action == "video-preview":
        run(PYTHON, "-m", "app.video_preview.cli", "--spec", "characters/sofia/video_concept.json", cwd=BACKEND)
    elif action == "down":
        run("docker", "compose", "down")
    else:
        print("Commands: setup, up, migrate, seed, test, check, dev, acceptance, visual-acceptance, delivery-acceptance, metrics-acceptance, community-acceptance, video-preview, down")


if __name__ == "__main__":
    main()
