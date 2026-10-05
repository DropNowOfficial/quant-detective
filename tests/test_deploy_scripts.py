from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]


def test_deploy_shell_scripts_parse():
    for rel in [
        "deploy/bootstrap-droplet.sh",
        "deploy/install-systemd.sh",
        "deploy/install-autoupdate.sh",
        "deploy/update-production.sh",
    ]:
        subprocess.run(["bash", "-n", str(ROOT / rel)], check=True)


def test_auto_update_timer_is_not_high_frequency():
    text=(ROOT/"deploy/systemd/quant-detective-update.timer").read_text()
    assert "OnUnitActiveSec=5min" in text
    assert "OnUnitActiveSec=1min" not in text


def test_auto_update_service_runs_fixed_local_script():
    text=(ROOT/"deploy/systemd/quant-detective-update.service").read_text()
    assert "ExecStart=/usr/local/sbin/quant-detective-update" in text
    assert "curl" not in text.lower()


def test_production_updater_requires_new_run_id():
    text=(ROOT/"deploy/update-production.sh").read_text()
    assert "old_run_id=" in text
    assert '"$run_id" != "$old_run_id"' in text
    assert "BOOTSTRAPPING" in text
