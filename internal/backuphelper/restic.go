// Package backuphelper is a trusted maintenance entry, never an Agent tool.
package backuphelper

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"os/exec"
	"regexp"
	"strings"

	"github.com/chx739/sandboxd/internal/workspace"
)

const MaxOutput = 2 << 20

var checkpointID = regexp.MustCompile(`^cp-[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}$`)
var fullHash = regexp.MustCompile(`^[a-f0-9]{64}$`)

type Request struct {
	Operation          string          `json:"operation"`
	CheckpointID       string          `json:"checkpointId,omitempty"`
	SnapshotID         string          `json:"snapshotId,omitempty"`
	ManifestSHA256     string          `json:"manifestSha256,omitempty"`
	RepositoryCryptoID string          `json:"repositoryCryptoId,omitempty"`
	Owner              RepositoryOwner `json:"owner,omitzero"`
}

func Parse(args []string) (Request, error) {
	var r Request
	if len(args) == 0 {
		return r, errors.New("missing fixed operation")
	}
	r.Operation = args[0]
	switch r.Operation {
	case "init-owned":
		if len(args) != 2 || !controllerID.MatchString(args[1]) {
			return r, errors.New("owned init requires controller UUIDv4")
		}
		r.Owner.ControllerID = args[1]
	case "backup-owned", "forget", "prune":
		n := 5
		if r.Operation == "prune" {
			n = 4
		}
		if len(args) != n {
			return r, errors.New("owned maintenance requires exact identities")
		}
		if r.Operation == "backup-owned" {
			r.Operation, r.CheckpointID = "backup", args[1]
			if !checkpointID.MatchString(r.CheckpointID) {
				return r, errors.New("invalid checkpoint identity")
			}
			r.Owner = RepositoryOwner{RepositoryCryptoID: args[2], ControllerID: args[3], OwnerSnapshotID: args[4]}
		} else {
			r.Owner = RepositoryOwner{RepositoryCryptoID: args[1], ControllerID: args[2], OwnerSnapshotID: args[3]}
			if r.Operation == "forget" {
				r.SnapshotID = args[4]
				if !fullHash.MatchString(r.SnapshotID) {
					return r, errors.New("forget requires full snapshot ID")
				}
			}
		}
		if !r.Owner.Valid() {
			return r, errors.New("invalid repository ownership binding")
		}
	case "manifest", "init", "snapshots", "stats", "check", "config":
		if len(args) != 1 {
			return r, errors.New("operation takes no arguments")
		}
	case "backup":
		if len(args) != 2 || !checkpointID.MatchString(args[1]) {
			return r, errors.New("backup requires UUIDv4 checkpoint ID")
		}
		r.CheckpointID = args[1]
	case "restore":
		if (len(args) != 3 && len(args) != 4) || !fullHash.MatchString(args[1]) || !fullHash.MatchString(args[2]) {
			return r, errors.New("restore requires full snapshot and manifest hashes")
		}
		r.SnapshotID, r.ManifestSHA256 = args[1], args[2]
		if len(args) == 4 {
			if !fullHash.MatchString(args[3]) {
				return r, errors.New("restore requires full repository identity")
			}
			r.RepositoryCryptoID = args[3]
		}
	default:
		return r, errors.New("unsupported maintenance operation")
	}
	return r, nil
}

type commandError struct {
	Code                     int
	StdoutBytes, StderrBytes int
}

func (e *commandError) Error() string {
	return fmt.Sprintf("restic command failed (exit=%d, stdoutBytes=%d, stderrBytes=%d)", e.Code, e.StdoutBytes, e.StderrBytes)
}

type bounded struct {
	bytes.Buffer
	overflow bool
}

func (b *bounded) Write(value []byte) (int, error) {
	if b.Len()+len(value) > MaxOutput {
		b.overflow = true
		return 0, errors.New("maintenance output exceeded limit")
	}
	return b.Buffer.Write(value)
}

// No caller-selected executable, repository, credentials, root or extra argv.
func command(ctx context.Context, action ...string) ([]byte, error) {
	args := []string{"--json", "--no-cache", "--repository-file", "/restic-secrets/repository", "--password-file", "/restic-secrets/password", "-o", "s3.connections=2", "-o", "s3.bucket-lookup=path"}
	cmd := exec.CommandContext(ctx, "/usr/local/bin/restic", append(args, action...)...)
	var out, stderr bounded
	cmd.Stdout, cmd.Stderr = &out, &stderr
	err := cmd.Run()
	if err != nil || out.overflow || stderr.overflow {
		code := -1
		var exit *exec.ExitError
		if errors.As(err, &exit) {
			code = exit.ExitCode()
		}
		// Never return raw stderr: S3/provider error messages may contain credentials.
		return nil, &commandError{Code: code, StdoutBytes: out.Len(), StderrBytes: stderr.Len()}
	}
	return out.Bytes(), nil
}

func objects(raw []byte) ([]map[string]any, error) {
	var result []map[string]any
	if err := json.Unmarshal(raw, &result); err != nil {
		return nil, errors.New("invalid restic snapshot JSON")
	}
	return result, nil
}

func tags(snapshot map[string]any) []string {
	var result []string
	items, _ := snapshot["tags"].([]any)
	for _, item := range items {
		if value, ok := item.(string); ok {
			result = append(result, value)
		}
	}
	return result
}

func hasTag(snapshot map[string]any, tag string) bool {
	for _, v := range tags(snapshot) {
		if v == tag {
			return true
		}
	}
	return false
}

func config(ctx context.Context) (map[string]any, error) {
	raw, err := command(ctx, "cat", "config")
	if err != nil {
		return nil, err
	}
	var value map[string]any
	if json.Unmarshal(raw, &value) != nil {
		return nil, errors.New("invalid restic config")
	}
	id, _ := value["id"].(string)
	if !fullHash.MatchString(id) {
		return nil, errors.New("missing full repository identity")
	}
	return map[string]any{"repositoryCryptoId": id, "repositoryVersion": value["version"]}, nil
}

func Run(ctx context.Context, r Request) (map[string]any, error) {
	result := map[string]any{"operation": r.Operation, "resticVersion": "0.18.1"}
	switch r.Operation {
	case "init-owned":
		return initializeOwned(ctx, r.Owner)
	case "forget", "prune":
		return cleanupOwned(ctx, r, command)
	case "manifest":
		value, err := workspace.SnapshotManifest("/workspace")
		result["manifest"] = value
		return result, err
	case "init":
		if _, err := command(ctx, "init", "--repository-version", "2"); err != nil {
			return nil, err
		}
		return config(ctx)
	case "config":
		return config(ctx)
	case "snapshots", "stats", "check":
		args := []string{r.Operation}
		if r.Operation == "stats" {
			args = append(args, "--mode", "raw-data")
		}
		if r.Operation == "check" {
			args = append(args, "--read-data")
		}
		raw, err := command(ctx, args...)
		if err != nil {
			return nil, err
		}
		var value any
		if json.Unmarshal(raw, &value) != nil {
			// check can emit JSON-lines. Keep only a checksum/size, not credentials or arbitrary logs.
			sum := sha256.Sum256(raw)
			value = map[string]any{"outputSha256": hex.EncodeToString(sum[:]), "outputBytes": len(raw)}
		}
		result["result"] = value
		return result, nil
	case "backup":
		if r.Owner != (RepositoryOwner{}) {
			if _, err := verifyOwner(ctx, r.Owner, command); err != nil {
				return nil, err
			}
		}
		before, err := workspace.SnapshotManifest("/workspace")
		if err != nil {
			return nil, err
		}
		tag := "checkpoint:" + r.CheckpointID
		raw, err := command(ctx, "snapshots", "--tag", tag)
		if err != nil {
			return nil, err
		}
		prior, err := objects(raw)
		if err != nil {
			return nil, err
		}
		if len(prior) > 1 {
			return nil, errors.New("ambiguous checkpoint snapshots")
		}
		var id string
		if len(prior) == 1 {
			if !hasTag(prior[0], "manifest:"+before.SHA256) {
				return nil, errors.New("checkpoint snapshot has a different manifest")
			}
			id, _ = prior[0]["id"].(string)
			result["reusedSnapshot"] = true
		} else {
			args := []string{"backup", "/workspace", "--host", "sandboxd-workspace", "--tag", tag, "--tag", "manifest:" + before.SHA256,
				"--exclude", "/workspace/.sandboxd-lock", "--exclude", ".sandboxd-tmp-*"}
			if r.Owner != (RepositoryOwner{}) {
				args = append(args, "--tag", ownerTag(r.Owner))
			}
			raw, err = command(ctx, args...)
			if err != nil {
				return nil, err
			} // Nonzero, including partial backup, never means success.
			for _, line := range bytes.Split(raw, []byte{'\n'}) {
				var value map[string]any
				if json.Unmarshal(line, &value) == nil && value["message_type"] == "summary" {
					id, _ = value["snapshot_id"].(string)
					stats := map[string]any{}
					for _, key := range []string{"files_new", "files_changed", "files_unmodified", "dirs_new", "dirs_changed", "dirs_unmodified", "data_blobs", "tree_blobs", "data_added", "data_added_packed", "total_files_processed", "total_bytes_processed", "total_duration"} {
						if v, ok := value[key]; ok {
							stats[key] = v
						}
					}
					result["summary"] = stats
				}
			}
		}
		if !fullHash.MatchString(id) {
			return nil, errors.New("restic omitted full snapshot ID")
		}
		after, err := workspace.SnapshotManifest("/workspace")
		if err != nil || before.SHA256 != after.SHA256 {
			return nil, errors.New("workspace changed during backup")
		}
		identity, err := config(ctx)
		if err != nil {
			return nil, err
		}
		result["repository"] = identity
		result["snapshotId"] = id
		result["manifest"] = after
		result["checkpointId"] = r.CheckpointID
		return result, nil
	case "restore":
		identity, err := config(ctx)
		if err != nil {
			return nil, err
		}
		if err = checkRestoreRepository(identity, r.RepositoryCryptoID); err != nil {
			return nil, err
		}
		// 在任何恢复写入之前检查仓库身份；同名bucket/错误Secret不能替换来源。
		before, err := workspace.SnapshotManifest("/workspace")
		if err != nil {
			return nil, err
		}
		if before.Entries != 0 {
			return nil, errors.New("restore target is not empty")
		}
		raw, err := command(ctx, "snapshots", r.SnapshotID)
		if err != nil {
			return nil, err
		}
		prior, err := objects(raw)
		if err != nil || len(prior) != 1 || prior[0]["id"] != r.SnapshotID || !hasTag(prior[0], "manifest:"+r.ManifestSHA256) {
			return nil, errors.New("snapshot identity or manifest does not match")
		}
		if _, err = command(ctx, "restore", r.SnapshotID+":/workspace", "--target", "/workspace", "--verify"); err != nil {
			return nil, err
		}
		after, err := workspace.SnapshotManifest("/workspace")
		if err != nil || after.SHA256 != r.ManifestSHA256 {
			return nil, errors.New("restored manifest does not match")
		}
		result["snapshotId"] = r.SnapshotID
		result["manifest"] = after
		result["repository"] = identity
		return result, nil
	default:
		return nil, errors.New("unvalidated maintenance operation")
	}
}

// FixedArgs is shared by the trusted Pod builder; identifiers are data, not shell.
func FixedArgs(r Request) ([]string, error) {
	args := []string{r.Operation}
	if r.Operation == "init-owned" {
		args = append(args, r.Owner.ControllerID)
	}
	if r.Operation == "forget" || r.Operation == "prune" {
		args = append(args, r.Owner.RepositoryCryptoID, r.Owner.ControllerID, r.Owner.OwnerSnapshotID)
		if r.Operation == "forget" {
			args = append(args, r.SnapshotID)
		}
	}
	if r.Operation == "backup" {
		args = append(args, r.CheckpointID)
		if r.Owner != (RepositoryOwner{}) {
			args[0] = "backup-owned"
			args = append(args, r.Owner.RepositoryCryptoID, r.Owner.ControllerID, r.Owner.OwnerSnapshotID)
		}
	}
	if r.Operation == "restore" {
		args = append(args, r.SnapshotID, r.ManifestSHA256)
		if r.RepositoryCryptoID != "" {
			args = append(args, r.RepositoryCryptoID)
		}
	}
	parsed, err := Parse(args)
	if err != nil || parsed != r {
		return nil, errors.New("invalid maintenance request fields")
	}
	for _, s := range args {
		if strings.ContainsAny(s, "\x00\n\r") {
			return nil, errors.New("invalid argument")
		}
	}
	return args, nil
}

func checkRestoreRepository(identity map[string]any, expected string) error {
	if expected != "" && (!fullHash.MatchString(expected) || identity["repositoryCryptoId"] != expected) {
		return errors.New("restore repository identity does not match")
	}
	return nil
}
