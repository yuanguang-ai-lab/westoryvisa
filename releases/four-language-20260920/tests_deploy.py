#!/usr/bin/env python3
"""Unit tests only: no Docker, network, production data or deployment mutations."""
import copy
import io
from pathlib import Path
import subprocess
import unittest
from unittest.mock import patch

import build_manifest
import deploy


def sample():
    return {"Config": {"Hostname": "old", "Image": "oldtag", "Labels": {"a": "b"},
                       "Env": ["SECRET=never-print-me"], "Cmd": ["nginx"], "AttachStdin": False,
                       "AttachStdout": True, "AttachStderr": True, "OpenStdin": False,
                       "Tty": False},
            "HostConfig": {"PortBindings": {"80/tcp": [{"HostPort": "8080"}]},
                           "RestartPolicy": {"Name": "unless-stopped"}, "Privileged": False,
                           "NetworkMode": "deploy_default", "Binds": None},
            "Mounts": [], "NetworkSettings": {"Networks": {
                "deploy_default": {"NetworkID": "net1", "IPAMConfig": None, "Links": None,
                                   "DriverOpts": None, "Aliases": ["frontend", "deploy-frontend-1"]}}}}


class SafetyTests(unittest.TestCase):
    def test_manifest_actual_source(self):
        manifest = deploy.manifest_verified()
        self.assertEqual((len(manifest["original_static"]), len(manifest["expected_static"])), (45, 48))
        self.assertEqual(len(manifest["payload"]), 16)

    def test_allowlist_exact(self):
        self.assertEqual(len(set(build_manifest.ALLOWLIST)), 16)
        self.assertNotIn("nginx.conf", build_manifest.ALLOWLIST)
        self.assertNotIn("backend.py", build_manifest.ALLOWLIST)

    def test_provenance_not_rewritten(self):
        manifest = build_manifest.generate()
        self.assertEqual(manifest["base_commit"], "e553b7f7fed5ed09af0914e006e6660dce3e477e")
        self.assertEqual(manifest["replacements"], 13)
        self.assertEqual(manifest["original_source_files"], 366)

    def test_runtime_does_not_retain_env(self):
        value = deploy.runtime(sample())
        self.assertNotIn("never-print-me", str(value))
        self.assertNotIn("SECRET", str(value))

    def test_runtime_environment_drift_detected(self):
        old = sample()
        new = copy.deepcopy(old)
        new["Config"]["Env"] = ["SECRET=changed"]
        self.assertNotEqual(deploy.runtime(old), deploy.runtime(new))

    def test_runtime_ignores_only_image_identity(self):
        old, new = sample(), sample()
        new["Config"].update({"Hostname": "new", "Image": "newtag", "Labels": {"different": "ok"}})
        self.assertEqual(deploy.runtime(old), deploy.runtime(new))

    def test_runtime_command_drift_detected(self):
        old, new = sample(), sample()
        new["Config"]["Cmd"] = ["unsafe"]
        self.assertNotEqual(deploy.runtime(old, True), deploy.runtime(new, True))

    def test_runtime_privilege_drift_detected(self):
        old, new = sample(), sample()
        new["HostConfig"]["Privileged"] = True
        self.assertNotEqual(deploy.runtime(old, True), deploy.runtime(new, True))

    def test_runtime_extra_network_detected(self):
        old, new = sample(), sample()
        new["NetworkSettings"]["Networks"]["other"] = {"NetworkID": "net2"}
        self.assertNotEqual(deploy.runtime(old, True), deploy.runtime(new, True))

    def test_runtime_mount_detected(self):
        old, new = sample(), sample()
        new["Mounts"] = [{"Source": "/data", "Destination": "/app/data"}]
        self.assertNotEqual(deploy.runtime(old, True), deploy.runtime(new, True))

    def test_oneoff_known_differences_only(self):
        old, new = sample(), sample()
        new["HostConfig"]["PortBindings"] = {"80/tcp": [{"HostIp": "127.0.0.1", "HostPort": "12345"}]}
        new["HostConfig"]["RestartPolicy"] = {"Name": "no"}
        new["Config"]["AttachStdout"] = False
        self.assertEqual(deploy.runtime(old, True), deploy.runtime(new, True))
        self.assertNotEqual(deploy.runtime(old), deploy.runtime(new))

    def test_aliases_explicit(self):
        self.assertEqual(deploy.network_aliases(sample())["deploy_default"], ["deploy-frontend-1", "frontend"])

    def test_hashes_exact(self):
        static, nginx = deploy.parse_hashes("a" * 64 + "  ./app.js\n" + "b" * 64 + "  " + deploy.NGINX_PATH + "\n")
        self.assertEqual(static, {"app.js": "a" * 64})
        self.assertEqual(nginx, "b" * 64)

    def test_hashes_duplicate_refused(self):
        with self.assertRaises(deploy.Refusal):
            deploy.parse_hashes(("a" * 64 + "  ./app.js\n") * 2)

    def test_hashes_path_escape_refused(self):
        with self.assertRaises(deploy.Refusal):
            deploy.parse_hashes("a" * 64 + "  ./../secret\n")

    def test_hashes_missing_nginx_refused(self):
        with self.assertRaises(deploy.Refusal):
            deploy.parse_hashes("a" * 64 + "  ./app.js\n")

    def test_metadata_separated_without_discarding_nested_paths(self):
        files = {"app.js": "a", "._app.js": "b", "assets/._logo.png": "c", "._metadata/entry": "d"}
        application, metadata = deploy.split_metadata(files)
        self.assertEqual(application, {"app.js": "a"})
        self.assertEqual(metadata, {"._app.js": "b", "assets/._logo.png": "c", "._metadata/entry": "d"})

    def test_original_metadata_collected_for_receipt(self):
        output = "a" * 64 + "  ./app.js\n" + "b" * 64 + "  ./._app.js\n" + "c" * 64 + "  " + deploy.NGINX_PATH + "\n"
        with patch.object(deploy, "run", side_effect=[output, ""]):
            metadata = deploy.check_hashes("original", {"app.js": "a" * 64}, "c" * 64)
        self.assertEqual(metadata, {"._app.js": "b" * 64})

    def test_candidate_metadata_drift_rejected(self):
        output = "a" * 64 + "  ./app.js\n" + "b" * 64 + "  ./._app.js\n" + "c" * 64 + "  " + deploy.NGINX_PATH + "\n"
        with patch.object(deploy, "run", return_value=output):
            with self.assertRaisesRegex(deploy.Refusal, "AppleDouble metadata"):
                deploy.check_hashes("candidate", {"app.js": "a" * 64}, "c" * 64, {"._app.js": "d" * 64})

    def test_added_metadata_rejected(self):
        output = "a" * 64 + "  ./app.js\n" + "b" * 64 + "  ./._app.js\n" + "c" * 64 + "  " + deploy.NGINX_PATH + "\n"
        with patch.object(deploy, "run", return_value=output):
            with self.assertRaisesRegex(deploy.Refusal, "AppleDouble metadata"):
                deploy.check_hashes("candidate", {"app.js": "a" * 64}, "c" * 64, {})

    def test_removed_metadata_rejected(self):
        output = "a" * 64 + "  ./app.js\n" + "c" * 64 + "  " + deploy.NGINX_PATH + "\n"
        with patch.object(deploy, "run", return_value=output):
            with self.assertRaisesRegex(deploy.Refusal, "AppleDouble metadata"):
                deploy.check_hashes("candidate", {"app.js": "a" * 64}, "c" * 64, {"._app.js": "b" * 64})

    def test_candidate_unchanged_metadata_accepted(self):
        output = "a" * 64 + "  ./app.js\n" + "b" * 64 + "  ./._app.js\n" + "c" * 64 + "  " + deploy.NGINX_PATH + "\n"
        metadata = {"._app.js": "b" * 64}
        with patch.object(deploy, "run", side_effect=[output, ""]):
            self.assertEqual(deploy.check_hashes("candidate", {"app.js": "a" * 64}, "c" * 64, metadata), metadata)

    def test_non_apple_hidden_file_still_refused(self):
        output = "a" * 64 + "  ./app.js\n" + "b" * 64 + "  ./.DS_Store\n" + "c" * 64 + "  " + deploy.NGINX_PATH + "\n"
        with patch.object(deploy, "run", return_value=output):
            with self.assertRaisesRegex(deploy.Refusal, "application file set"):
                deploy.check_hashes("candidate", {"app.js": "a" * 64}, "c" * 64, {})

    def test_clean_rollback_metadata_drift_rejected(self):
        output = "a" * 64 + "  ./app.js\n" + "b" * 64 + "  ./._app.js\n" + "c" * 64 + "  " + deploy.NGINX_PATH + "\n"
        manifest = {"original_static": {"app.js": "a" * 64}, "nginx_sha256": "c" * 64}
        with patch.object(deploy, "run", return_value=output):
            with self.assertRaisesRegex(deploy.Refusal, "AppleDouble metadata"):
                deploy.check_clean_base("old-image", manifest, {"._app.js": "d" * 64})

    def test_subprocess_error_redacted(self):
        response = subprocess.CompletedProcess([], 1, b"SECRET=password", b"TOKEN=private")
        with patch.object(deploy.subprocess, "run", return_value=response):
            with self.assertRaisesRegex(deploy.Refusal, "^Command failed: docker$"):
                deploy.run(["docker", "secret-argument"])

    def test_compose_cannot_remove_orphans_from_environment(self):
        response = subprocess.CompletedProcess([], 0, b"{}", b"")
        with patch.object(deploy.subprocess, "run", return_value=response) as command:
            deploy.run(["docker", "compose", "config"])
            env = command.call_args.kwargs["env"]
            self.assertEqual(env["COMPOSE_REMOVE_ORPHANS"], "false")
            self.assertEqual(env["COMPOSE_IGNORE_ORPHANS"], "true")

    def test_rollback_recovers_absent_frontend(self):
        record = {"compose": {"project": "deploy"}, "candidate_id": "candidate", "old_image": "old",
                  "new_image": "new", "other_containers": {"backend": {}}, "phase": "activation_failed"}
        manifest = {"original_static": {"app.js": "hash"}}
        with patch.object(deploy, "inspect_optional", return_value=None), \
                patch.object(deploy, "run", return_value=""), \
                patch.object(deploy, "all_containers", return_value={"backend": {}}), \
                patch.object(deploy, "compose_up") as up, \
                patch.object(deploy, "verify_with_retry", return_value={"Id": "restored"}), \
                patch.object(deploy, "save_receipt"), patch.object(deploy, "status"):
            deploy.rollback(record, Path("/safe-state"), manifest, automatic=True)
            up.assert_called_once_with(record, Path("/safe-state"), "rollback-image.yml")
            self.assertEqual(record["phase"], "rolled_back")

    def test_rollback_refuses_unrelated_frontend_image(self):
        record = {"old_image": "old", "new_image": "new"}
        with patch.object(deploy, "inspect_optional", return_value={"Image": "unrelated"}):
            with self.assertRaises(deploy.Refusal):
                deploy.rollback(record, Path("/safe-state"), {})

    def test_health_refuses_unknown_jobs(self):
        with patch.object(deploy, "http", return_value=(200, b'{"ok":true,"apiRevision":22,"agent":{"ready":true,"connected":true}}')):
            with self.assertRaises(deploy.Refusal):
                deploy.health("http://local", True)

    def test_health_refuses_active_jobs(self):
        with patch.object(deploy, "http", return_value=(200, b'{"ok":true,"apiRevision":22,"agent":{"ready":true,"connected":true,"activeJobs":1}}')):
            with self.assertRaises(deploy.Refusal):
                deploy.health("http://local", True)

    def test_health_accepts_idle(self):
        with patch.object(deploy, "http", return_value=(200, b'{"ok":true,"apiRevision":22,"agent":{"ready":true,"connected":true,"activeJobs":0}}')):
            self.assertEqual(deploy.health("http://local", True)["active_jobs"], 0)

    def test_health_refuses_revision20(self):
        with patch.object(deploy, "http", return_value=(200, b'{"ok":true,"apiRevision":20}')):
            with self.assertRaises(deploy.Refusal):
                deploy.health("http://local")


if __name__ == "__main__":
    unittest.main()
