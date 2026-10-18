package backuphelper

import (
	"bytes"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"errors"
	"os"
	"regexp"
	"slices"
	"strings"
)

var controllerID = regexp.MustCompile(`^controller-[a-f0-9]{12}4[a-f0-9]{3}[89ab][a-f0-9]{15}$`)

const ownerMarkerTag = "sandboxd-owner-marker"
const ownerMarkerFile = "/tmp/sandboxd-owner.json"

// RepositoryOwner来自部署者固定配置；不是Agent提供的删除授权。
type RepositoryOwner struct {
	RepositoryCryptoID string `json:"repositoryCryptoId"`
	ControllerID       string `json:"controllerId"`
	OwnerSnapshotID    string `json:"ownerSnapshotId"`
}

func (o RepositoryOwner) Valid() bool {
	return fullHash.MatchString(o.RepositoryCryptoID) && controllerID.MatchString(o.ControllerID) && fullHash.MatchString(o.OwnerSnapshotID)
}
func ownerTag(o RepositoryOwner) string { return "controller:" + o.ControllerID }

type commandRunner func(context.Context, ...string) ([]byte, error)

func initializeOwned(ctx context.Context, owner RepositoryOwner) (map[string]any, error) {
	if !controllerID.MatchString(owner.ControllerID) || owner.RepositoryCryptoID != "" || owner.OwnerSnapshotID != "" {
		return nil, errors.New("invalid new repository owner")
	}
	// 仅fresh init；已存在仓库失败，不给旧共享数据自动补归属标记。
	if _, err := command(ctx, "init", "--repository-version", "2"); err != nil {
		return nil, err
	}
	marker := struct {
		Version      int    `json:"schemaVersion"`
		ControllerID string `json:"controllerId"`
	}{1, owner.ControllerID}
	raw, _ := json.Marshal(marker)
	file, err := os.OpenFile(ownerMarkerFile, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0600)
	if err != nil {
		return nil, errors.New("private owner marker creation failed")
	}
	if _, err = file.Write(raw); err != nil {
		file.Close()
		return nil, errors.New("owner marker write failed")
	}
	if err = file.Close(); err != nil {
		return nil, errors.New("owner marker close failed")
	}
	raw, err = command(ctx, "backup", ownerMarkerFile, "--host", "sandboxd-owner", "--tag", ownerMarkerTag, "--tag", ownerTag(owner))
	if err != nil {
		return nil, err
	}
	for _, line := range bytes.Split(raw, []byte{'\n'}) {
		var v map[string]any
		if json.Unmarshal(line, &v) == nil && v["message_type"] == "summary" {
			owner.OwnerSnapshotID, _ = v["snapshot_id"].(string)
		}
	}
	identity, err := config(ctx)
	if err != nil {
		return nil, err
	}
	owner.RepositoryCryptoID, _ = identity["repositoryCryptoId"].(string)
	if _, err = verifyOwner(ctx, owner, command); err != nil {
		return nil, err
	}
	return map[string]any{"operation": "init-owned", "owner": owner, "repository": identity, "resticVersion": "0.18.1"}, nil
}

// verifyOwner在任何删除前验证crypto、不可删除的marker以及全部快照归属。
// 未标记/其他控制器的快照使整个清理拒绝；不手工改S3对象或强制unlock。
func verifyOwner(ctx context.Context, owner RepositoryOwner, run commandRunner) ([]string, error) {
	if !owner.Valid() {
		return nil, errors.New("missing repository ownership binding")
	}
	raw, err := run(ctx, "cat", "config")
	if err != nil {
		return nil, err
	}
	var cfg struct {
		ID string `json:"id"`
	}
	if json.Unmarshal(raw, &cfg) != nil || cfg.ID != owner.RepositoryCryptoID {
		return nil, errors.New("cleanup repository identity mismatch")
	}
	raw, err = run(ctx, "snapshots")
	if err != nil {
		return nil, err
	}
	list, err := objects(raw)
	if err != nil {
		return nil, err
	}
	ids, markerCount := []string{}, 0
	for _, snapshot := range list {
		id, _ := snapshot["id"].(string)
		if !fullHash.MatchString(id) || slices.Contains(ids, id) || !hasTag(snapshot, ownerTag(owner)) {
			return nil, errors.New("foreign or invalid repository snapshot")
		}
		count := 0
		for _, tag := range tags(snapshot) {
			if strings.HasPrefix(tag, "controller:") {
				if tag != ownerTag(owner) {
					return nil, errors.New("ambiguous snapshot controller")
				}
				count++
			}
		}
		if count != 1 {
			return nil, errors.New("ambiguous snapshot controller")
		}
		ids = append(ids, id)
		if hasTag(snapshot, ownerMarkerTag) {
			if id != owner.OwnerSnapshotID {
				return nil, errors.New("ambiguous repository ownership marker")
			}
			markerCount++
		}
	}
	if markerCount != 1 {
		return nil, errors.New("repository ownership marker missing")
	}
	raw, err = run(ctx, "dump", owner.OwnerSnapshotID, ownerMarkerFile)
	if err != nil {
		return nil, err
	}
	var marker struct {
		Version      int    `json:"schemaVersion"`
		ControllerID string `json:"controllerId"`
	}
	if json.Unmarshal(raw, &marker) != nil || marker.Version != 1 || marker.ControllerID != owner.ControllerID {
		return nil, errors.New("repository owner marker mismatch")
	}
	slices.Sort(ids)
	return ids, nil
}

func cleanupOwned(ctx context.Context, request Request, run commandRunner) (map[string]any, error) {
	if _, err := FixedArgs(request); err != nil {
		return nil, err
	}
	before, err := verifyOwner(ctx, request.Owner, run)
	if err != nil {
		return nil, err
	}
	result := map[string]any{"operation": request.Operation, "owner": request.Owner, "resticVersion": "0.18.1"}
	if request.Operation == "forget" {
		if request.SnapshotID == request.Owner.OwnerSnapshotID {
			return nil, errors.New("ownership marker is protected")
		}
		present := slices.Contains(before, request.SnapshotID)
		if present {
			if _, err = run(ctx, "forget", request.SnapshotID); err != nil {
				return nil, err
			}
		}
		after, err := verifyOwner(ctx, request.Owner, run)
		if err != nil {
			return nil, err
		}
		expected := slices.DeleteFunc(slices.Clone(before), func(id string) bool { return id == request.SnapshotID })
		if !slices.Equal(after, expected) {
			return nil, errors.New("forget repository inventory changed unexpectedly")
		}
		result["snapshotId"], result["forgotten"], result["alreadyAbsent"] = request.SnapshotID, true, !present
	} else if request.Operation == "prune" {
		raw, err := run(ctx, "prune", "--max-unused", "0")
		if err != nil {
			return nil, err
		}
		after, err := verifyOwner(ctx, request.Owner, run)
		if err != nil || !slices.Equal(after, before) {
			return nil, errors.New("prune changed snapshot inventory or ownership")
		}
		sum := sha256.Sum256(raw)
		result["pruned"], result["outputBytes"], result["outputSha256"] = true, len(raw), hex.EncodeToString(sum[:])
	} else {
		return nil, errors.New("invalid cleanup operation")
	}
	return result, nil
}
