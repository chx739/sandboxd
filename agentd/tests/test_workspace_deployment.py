"""V2部署状态机的合成协议测试；无Kubernetes/restic/模型调用。"""
import copy
from datetime import datetime, timezone
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from agentd import workspace_deployment as d


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.home = Path(temporary.name)
        self.deployments = self.home / "deployments"
        self.root = self.deployments / "demo"
        self.private = self.home / "minio.json"
        d.save(self.private, {"access": "synthetic-access", "secret": "synthetic-secret"})
        self.objects = {}
        self.init_creates = 0
        self.fail_after_create = False
        self.fail_bind = False
        self.owner = None
        self.calls = []
        for target, value in (("DEPLOYMENTS", self.deployments), ("CREDENTIALS", self.private)):
            p = patch.object(d, target, value); p.start(); self.addCleanup(p.stop)
        for target, value in (("endpoints", lambda: {"nfsIp": "172.23.0.3", "minioIp": "172.23.0.4"}),
                              ("command", self.command), ("get", self.get), ("bind_agent", self.bind)):
            p = patch.object(d, target, value); p.start(); self.addCleanup(p.stop)
        p = patch.object(Path, "home", return_value=self.home); p.start(); self.addCleanup(p.stop)

    def get(self, kind, name, namespace="sandboxd-storage"):
        if kind == "node":
            return {"status": {"conditions": [{"type": "Ready", "status": "True"}]}}
        if kind == "lease":
            return {"spec": {"renewTime": datetime.now(timezone.utc).isoformat()}}
        return copy.deepcopy(self.objects.get((kind.lower(), name)))

    def command(self, args, *, body=None, timeout=40):
        if args[0].endswith("workspace-backup-pod-owned"):
            job, secret = args[args.index("-id")+1], args[args.index("-secret")+1]
            self.owner = {"controllerId": args[args.index("-controller")+1], "repositoryCryptoId": "a"*64, "ownerSnapshotId": "b"*64}
            return d.encode({"pod": {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "workspace-backup-"+job, "namespace": "sandboxd-storage", "creationTimestamp": None},
                "spec": {"runtimeClassName": "gvisor", "containers": [{"image": d.IMAGE, "args": ["init-owned", self.owner["controllerId"]]}]}},
                "networkPolicy": {"apiVersion": "networking.k8s.io/v1", "kind": "NetworkPolicy", "metadata": {"name": "backup-minio-only", "namespace": "sandboxd-storage", "creationTimestamp": None}, "spec": {"policyTypes": ["Egress"]}}})
        self.calls.append("create-" + body["kind"] if "create" in args else "logs" if "logs" in args else "delete")
        if "create" in args:
            value = copy.deepcopy(body)
            value["metadata"]["uid"] = "synthetic-uid"
            if body["kind"] == "Pod":
                self.init_creates += 1
                value["spec"]["nodeName"] = "sandboxd-control-plane"
                value["status"] = {"phase": "Succeeded", "containerStatuses": [{"imageID": "sha256:"+"c"*64, "state": {"terminated": {"exitCode": 0}}}]}
            self.objects[(body["kind"].lower(), body["metadata"]["name"])] = value
            if body["kind"] == "Pod" and self.fail_after_create:
                self.fail_after_create = False
                raise TimeoutError("synthetic lost create ACK")
            return d.encode(value)
        if "logs" in args:
            return d.encode({"ok": True, "operation": "init-owned", "resticVersion": "0.18.1", "owner": self.owner})
        if "delete" in args:
            name = args[args.index("--raw")+1].split("/")[-1]
            self.assertEqual(body["preconditions"]["uid"], self.objects[("pod", name)]["metadata"]["uid"])
            del self.objects[("pod", name)]
            return b"{}"
        raise AssertionError("unexpected command")

    def bind(self, root, state):
        if self.fail_bind:
            raise RuntimeError("synthetic bind interruption")
        checkpoint = root / "agent-control/checkpoints"
        checkpoint.mkdir(mode=0o700, exist_ok=True)
        catalogue = self.home / ".local/share/sandboxd/repository-controllers"
        catalogue.mkdir(parents=True, mode=0o700, exist_ok=True)
        d.save(catalogue/(state["owner"]["repositoryCryptoId"]+".json"), {"schemaVersion": 1, "owner": state["owner"], **d.directory(checkpoint)})

    def test_fresh_then_repeated_init_preserves_all_identities_and_bytes(self):
        result = d.initialize(self.root)
        original = (self.root / "deployment.json").read_bytes()
        credentials = (self.root / "repository-credentials.json").read_bytes()
        self.assertEqual(result, d.initialize(self.root))
        self.assertEqual(original, (self.root / "deployment.json").read_bytes())
        self.assertEqual(credentials, (self.root / "repository-credentials.json").read_bytes())
        self.assertEqual(self.init_creates, 1)
        self.assertNotIn("synthetic-secret", original.decode())
        self.assertNotIn("password", original.decode())
        self.assertEqual(list(self.objects), [("secret", result["secretName"]), ("networkpolicy", "backup-minio-only")])

    def test_lost_create_ack_reconciles_same_pod_without_reinit(self):
        self.fail_after_create = True
        with self.assertRaises(TimeoutError): d.initialize(self.root)
        state = d.load(self.root)
        self.assertEqual(state["phase"], "INIT_SUBMITTED")
        result = d.initialize(self.root)
        self.assertEqual(result["jobId"], state["jobId"])
        self.assertEqual(self.init_creates, 1)

    def test_missing_submitted_pod_stops_without_recreating(self):
        self.fail_after_create = True
        with self.assertRaises(TimeoutError): d.initialize(self.root)
        self.objects = {k: v for k, v in self.objects.items() if k[0] != "pod"}
        with self.assertRaisesRegex(d.DeploymentError, "missing"): d.initialize(self.root)
        self.assertEqual(self.init_creates, 1)
        with self.assertRaises(d.DeploymentError): d.ready(self.root)

    def test_owner_ready_interruption_retries_binding_only(self):
        self.fail_bind = True
        with self.assertRaises(RuntimeError): d.initialize(self.root)
        self.assertEqual(d.load(self.root)["phase"], "OWNER_READY")
        self.fail_bind = False
        d.initialize(self.root)
        self.assertEqual(self.init_creates, 1)

    def test_changed_pod_uid_is_rejected(self):
        self.fail_bind = True
        with self.assertRaises(RuntimeError): d.initialize(self.root)
        name = d.load(self.root)["proof"]["podName"]
        self.objects[("pod", name)]["metadata"]["uid"] = "replacement-uid"
        self.fail_bind = False
        with self.assertRaisesRegex(d.DeploymentError, "identity"): d.initialize(self.root)
        self.assertEqual(self.init_creates, 1)

    def test_failed_pod_keeps_original_intent(self):
        self.fail_after_create = True
        with self.assertRaises(TimeoutError): d.initialize(self.root)
        name = d.load(self.root)["podTemplate"]["metadata"]["name"]
        self.objects[("pod", name)]["status"]["phase"] = "Failed"
        with self.assertRaisesRegex(d.DeploymentError, "successfully"): d.initialize(self.root)
        self.assertEqual(self.init_creates, 1)

    def test_changed_secret_refused_without_overwrite(self):
        state = d.initialize(self.root)
        self.objects[("secret", state["secretName"])]["data"]["password"] = "different"
        with self.assertRaisesRegex(d.DeploymentError, "differs"): d.initialize(self.root)
        self.assertEqual(self.init_creates, 1)

    def test_missing_credentials_never_rotated_after_submit(self):
        self.fail_after_create = True
        with self.assertRaises(TimeoutError): d.initialize(self.root)
        (self.root / "repository-credentials.json").unlink()
        with self.assertRaisesRegex(d.DeploymentError, "do not rotate"): d.initialize(self.root)

    def test_changed_storage_address_stops(self):
        d.initialize(self.root)
        with patch.object(d, "endpoints", return_value={"nfsIp": "172.23.0.9", "minioIp": "172.23.0.4"}):
            with self.assertRaisesRegex(d.DeploymentError, "endpoint changed"): d.initialize(self.root)

    def test_prior_history_not_adopted(self):
        d.root_path(self.root, create=True)
        (self.root / "old.jsonl").write_text("history")
        with self.assertRaisesRegex(d.DeploymentError, "prior files"): d.initialize(self.root)
        self.assertEqual(self.init_creates, 0)

    def test_state_checksum_corruption_rejected(self):
        d.initialize(self.root)
        value = json.loads((self.root / "deployment.json").read_text())
        value["state"]["secretName"] = "restic-"+"d"*32
        d.save(self.root / "deployment.json", value)
        with self.assertRaisesRegex(d.DeploymentError, "checksum"): d.ready(self.root)

    def test_moved_control_directory_or_wrong_claim_rejected(self):
        state = d.initialize(self.root)
        old = self.root / "go-control"
        old.rename(self.root / "old-go")
        old.mkdir(mode=0o700)
        with self.assertRaisesRegex(d.DeploymentError, "moved"): d.ready(self.root)
        old.rmdir(); (self.root / "old-go").rename(old)
        claim = self.home / ".local/share/sandboxd/repository-controllers" / (state["owner"]["repositoryCryptoId"]+".json")
        value = d.private_read(claim); value["inode"] += 1; d.save(claim, value)
        with self.assertRaisesRegex(d.DeploymentError, "claim differs"): d.ready(self.root)

    def test_symlink_directory_and_broad_permissions_rejected(self):
        d.root_path(self.root, create=True)
        self.root.chmod(0o755)
        with self.assertRaises(d.DeploymentError): d.initialize(self.root)
        self.root.chmod(0o700)
        link = self.deployments / "link"
        link.symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(d.DeploymentError): d.initialize(link)

    def test_parallel_initializer_lock_refused(self):
        d.root_path(self.root, create=True)
        with d.locked(self.root):
            with self.assertRaises(BlockingIOError): d.initialize(self.root)

    def test_fixed_views_contain_no_credentials_or_free_arguments(self):
        state = d.initialize(self.root)
        args = d.view(state, "go-args")
        self.assertEqual(args[args.index("-workspace-backup-controller")+1], state["owner"]["controllerId"])
        self.assertEqual(d.view(state, "agent-env"), ["AGENTD_RECOVERY_DIR="+str(self.root / "agent-control"),
            "AGENTD_CHECKPOINT_MODE=restic", "AGENTD_CHECKPOINT_REPOSITORY_ID=repo-"+state["controllerId"][11:]])
        self.assertNotIn("synthetic-secret", "\n".join(args))

    def test_unready_configuration_view_does_not_advance_initialization(self):
        self.fail_after_create = True
        with self.assertRaises(TimeoutError): d.initialize(self.root)
        original = (self.root / "deployment.json").read_bytes()
        with self.assertRaisesRegex(d.DeploymentError, "not ready"): d.ready(self.root)
        self.assertEqual(original, (self.root / "deployment.json").read_bytes())
        self.assertEqual(self.init_creates, 1)
        output = io.StringIO()
        with patch.object(sys, "argv", ["deployment", "status", "--deployment-dir", str(self.root)]), patch("sys.stdout", output):
            d.main()
        self.assertEqual(json.loads(output.getvalue())["phase"], "INIT_SUBMITTED")
        self.assertEqual(original, (self.root / "deployment.json").read_bytes())

    def test_unhealthy_node_stops_then_same_pod_can_be_reconciled(self):
        original = self.get
        def get(kind, name, namespace="sandboxd-storage"):
            if kind == "node":
                return {"status": {"conditions": [{"type": "Ready", "status": "False"}]}}
            return original(kind, name, namespace)
        with patch.object(d, "get", get):
            with self.assertRaisesRegex(d.DeploymentError, "termination proof"): d.initialize(self.root)
        self.assertEqual(d.load(self.root)["phase"], "INIT_SUBMITTED")
        d.initialize(self.root)
        self.assertEqual(self.init_creates, 1)

    def test_expired_lease_cannot_prove_stop(self):
        original = self.get
        def get(kind, name, namespace="sandboxd-storage"):
            if kind == "lease":
                return {"spec": {"renewTime": "2000-01-01T00:00:00Z"}}
            return original(kind, name, namespace)
        with patch.object(d, "get", get):
            with self.assertRaisesRegex(d.DeploymentError, "termination proof"): d.initialize(self.root)
        self.assertEqual(self.init_creates, 1)

    def test_cli_views_are_read_only_and_reject_changed_endpoint(self):
        d.initialize(self.root)
        before = (self.root / "deployment.json").read_bytes()
        output = io.StringIO()
        with patch.object(sys, "argv", ["deployment", "go-args", "--deployment-dir", str(self.root)]), patch("sys.stdout", output):
            d.main()
        self.assertIn("-workspace-backup-owner-snapshot\n"+"b"*64, output.getvalue())
        self.assertEqual(before, (self.root / "deployment.json").read_bytes())
        with patch.object(sys, "argv", ["deployment", "go-args", "--deployment-dir", str(self.root)]), \
                patch.object(d, "endpoints", return_value={"nfsIp": "172.23.0.99", "minioIp": "172.23.0.4"}), patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as error: d.main()
        self.assertEqual(error.exception.code, 1)
