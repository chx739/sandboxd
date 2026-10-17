// Trusted component fixture/spec renderer, never registered as an Agent tool.
package main

import (
	"encoding/json"
	"flag"
	"log"
	"os"

	"github.com/chx739/sandboxd/internal/backuphelper"
	"github.com/chx739/sandboxd/internal/sandbox"
)

func main() {
	image := flag.String("image", "sandboxd-restic:0.18.1-v1", "verified maintenance image")
	id := flag.String("id", "", "generated maintenance Pod id")
	workspace := flag.String("workspace", "", "trusted workspace identity")
	secret := flag.String("secret", "", "trusted credentials Secret name")
	endpoint := flag.String("endpoint", "", "explicit MinIO IPv4")
	op := flag.String("operation", "", "fixed operation")
	cp := flag.String("checkpoint", "", "UUIDv4 checkpoint identity")
	snapshot := flag.String("snapshot", "", "full snapshot hash")
	manifest := flag.String("manifest", "", "expected restore manifest hash")
	flag.Parse()
	request := backuphelper.Request{Operation: *op, CheckpointID: *cp, SnapshotID: *snapshot, ManifestSHA256: *manifest}
	pod, err := sandbox.BuildBackupPod(sandbox.Config{Image: *image, RuntimeClass: "gvisor"}, *id, *workspace, *secret, request)
	if err != nil {
		log.Fatal(err)
	}
	policy, err := sandbox.BuildBackupNetworkPolicy(*endpoint)
	if err != nil {
		log.Fatal(err)
	}
	json.NewEncoder(os.Stdout).Encode(map[string]any{"pod": pod, "networkPolicy": policy})
}
