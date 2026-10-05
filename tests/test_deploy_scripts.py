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


def test_auto_update_timer_is_low_frequency_fallback():
    text=(ROOT/"deploy/systemd/quant-detective-update.timer").read_text()
    assert "OnUnitInactiveSec=60s" in text
    assert "OnUnitInactiveSec=1s" not in text


def test_auto_update_service_runs_fixed_local_script():
    text=(ROOT/"deploy/systemd/quant-detective-update.service").read_text()
    assert "ExecStart=/usr/local/sbin/quant-detective-update" in text
    assert "curl" not in text.lower()


def test_production_updater_requires_new_run_id():
    text=(ROOT/"deploy/update-production.sh").read_text()
    assert "old_run_id=" in text
    assert '"$run_id" != "$old_run_id"' in text
    assert "BOOTSTRAPPING" in text


def test_watchdog_assets_and_updater_install_path_exist():
    assert (ROOT/"deploy/watchdog.py").exists()
    service=(ROOT/"deploy/systemd/quant-detective-watchdog.service").read_text()
    timer=(ROOT/"deploy/systemd/quant-detective-watchdog.timer").read_text()
    updater=(ROOT/"deploy/update-production.sh").read_text()
    assert "watchdog.py 180 45" in service
    assert "OnUnitInactiveSec=60s" in timer
    assert "quant-detective-watchdog.timer" in updater
    assert "quant-detective-watchdog.service" in updater


def test_new_release_operational_units_reconcile_after_health_passes():
    text=(ROOT/"deploy/update-production.sh").read_text()
    health_pos=text.index('if [ "$ready" -ne 1 ]')
    reconcile_pos=text.rindex('reconcile_operational_units "$release"')
    assert reconcile_pos > health_pos


def test_already_current_release_reconciles_operational_units():
    text=(ROOT/"deploy/update-production.sh").read_text()
    assert "reconcile_operational_units()" in text
    current_block=text[text.index('if [ "$current_sha" = "$remote_sha" ]'):text.index('release="$RELEASES/$remote_sha"')]
    assert 'reconcile_operational_units "$current_target"' in current_block


def test_github_fallback_dispatch_timer_is_wired_safely():
    service=(ROOT/"deploy/systemd/quant-detective-github-fallback.service").read_text()
    timer=(ROOT/"deploy/systemd/quant-detective-github-fallback.timer").read_text()
    env=(ROOT/"deploy/systemd/github-fallback.env.example").read_text()
    updater=(ROOT/"deploy/update-production.sh").read_text()
    assert "EnvironmentFile=-/etc/quant-detective/github-fallback.env" in service
    assert "SuccessExitStatus=3" in service
    assert "OnUnitInactiveSec=5min" in timer
    assert "QD_GITHUB_FALLBACK_TOKEN=" in env
    assert "quant-detective-github-fallback.timer" in updater
    assert "github-fallback.env.example" in updater
