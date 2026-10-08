# Learning engine scheduler templates (not installed, not enabled)

Nothing in this repository installs, loads, enables or starts any of these. They are templates for a human to copy and fill.

* `run_daily.sh` - validates the config first, then one run. Needs `KEMET_LE_REPO`, `KEMET_LE_CONFIG`, `KEMET_LE_STATE` from the
  scheduler's own configuration. Offline unless the config says otherwise.
* `com.kemetfc.learning-engine.plist.template` - macOS launchd job, `Disabled` true. launchd has no per-job time zone, so the
  06:30 trigger is the Mac's local time. Tested: the template is valid XML/plist (`plutil -lint`) once placeholders are filled.
* `kemet-learning-engine.service.template` and `.timer.template` - systemd. `OnCalendar=*-*-* 06:30:00 America/Detroit`. NOT
  tested on Linux or systemd: I cannot confirm they work as written.

The templates are config-driven: they run whatever `discovery_mode` the config says (`allowlisted_channels` or `public_keyword_search`) and stay offline unless the config is live. A real daily schedule needs a chosen host and a separate decision to enable it; none is installed.

The daily `run` also performs the local knowledge-pipeline pass (queue triage and, when an analyzer is plugged in by code, bounded analysis) if `pipeline.enabled` is true in the config; it uses no network and no paid service.

Operating reality: a local schedule only runs while the computer is on (and, for launchd, the user is logged in). A closed
laptop runs nothing; launchd does not make up a missed run, systemd with `Persistent=true` runs a missed day once. Either way
the engine is idempotent per day and refuses to overlap, so catching up is safe.
