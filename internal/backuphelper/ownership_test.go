package backuphelper

import (
	"context"
	"encoding/json"
	"errors"
	"testing"
)

const testController = "controller-123456789abc4def8123456789abcdef"

func TestOwnedFixedArgs(t *testing.T) {
	owner := RepositoryOwner{string64("a"), testController, string64("b")}
	for _, r := range []Request{
		{Operation: "init-owned", Owner: RepositoryOwner{ControllerID: testController}},
		{Operation: "backup", CheckpointID: testCheckpoint, Owner: owner},
		{Operation: "forget", SnapshotID: string64("c"), Owner: owner},
		{Operation: "prune", Owner: owner},
	} {
		args, err := FixedArgs(r)
		if err != nil {
			t.Fatal(err)
		}
		got, err := Parse(args)
		if err != nil || got != r {
			t.Fatal(got, err)
		}
	}
	for _, args := range [][]string{{"forget", "latest"}, {"prune"}, {"init-owned", "../other"},
		{"forget", string64("a"), testController, string64("b"), "latest"}, {"backup-owned", testCheckpoint, "s3:other", testController, string64("b")}} {
		if _, err := Parse(args); err == nil {
			t.Fatal("accepted invalid owner arguments")
		}
	}
	if _, err := FixedArgs(Request{Operation: "prune", Owner: owner, SnapshotID: string64("c")}); err == nil {
		t.Fatal("unused snapshot accepted")
	}
}

// 假仓库只验证固定子命令与拒绝时序，不冒充MinIO或真实restic结果。
type fakeOwnedRepo struct {
	owner        RepositoryOwner
	snapshots    []map[string]any
	mutations    []string
	failMutation bool
}

func (f *fakeOwnedRepo) run(_ context.Context, args ...string) ([]byte, error) {
	switch args[0] {
	case "cat":
		return json.Marshal(map[string]any{"id": f.owner.RepositoryCryptoID})
	case "snapshots":
		return json.Marshal(f.snapshots)
	case "dump":
		return json.Marshal(map[string]any{"schemaVersion": 1, "controllerId": f.owner.ControllerID})
	case "forget":
		f.mutations = append(f.mutations, args[0])
		if f.failMutation {
			return nil, errors.New("synthetic failure")
		}
		kept := []map[string]any{}
		for _, v := range f.snapshots {
			if v["id"] != args[1] {
				kept = append(kept, v)
			}
		}
		f.snapshots = kept
		return []byte("[]"), nil
	case "prune":
		f.mutations = append(f.mutations, args[0])
		return []byte("synthetic prune result"), nil
	}
	return nil, errors.New("unexpected subcommand")
}
func fakeOwned() *fakeOwnedRepo {
	o := RepositoryOwner{string64("a"), testController, string64("b")}
	return &fakeOwnedRepo{owner: o, snapshots: []map[string]any{
		{"id": o.OwnerSnapshotID, "tags": []any{ownerTag(o), ownerMarkerTag}},
		{"id": string64("c"), "tags": []any{ownerTag(o), "checkpoint:" + testCheckpoint, "manifest:" + string64("d")}},
	}}
}

func TestOwnedCleanupExactForgetRetryAndPrune(t *testing.T) {
	f := fakeOwned()
	ctx := context.Background()
	r := Request{Operation: "forget", SnapshotID: string64("c"), Owner: f.owner}
	result, err := cleanupOwned(ctx, r, f.run)
	if err != nil || result["forgotten"] != true || len(f.snapshots) != 1 {
		t.Fatal(result, err)
	}
	result, err = cleanupOwned(ctx, r, f.run)
	if err != nil || result["alreadyAbsent"] != true || len(f.mutations) != 1 {
		t.Fatal(result, err)
	}
	result, err = cleanupOwned(ctx, Request{Operation: "prune", Owner: f.owner}, f.run)
	if err != nil || result["pruned"] != true || len(f.snapshots) != 1 {
		t.Fatal(result, err)
	}
}

func TestOwnedCleanupRejectsBeforeMutation(t *testing.T) {
	for _, name := range []string{"wrong-repository", "wrong-controller", "foreign-snapshot", "missing-marker", "marker-target", "bad-marker-body", "ambiguous-controller"} {
		t.Run(name, func(t *testing.T) {
			f := fakeOwned()
			r := Request{Operation: "forget", SnapshotID: string64("c"), Owner: f.owner}
			switch name {
			case "ambiguous-controller":
				f.snapshots[1]["tags"] = []any{ownerTag(f.owner), "controller:other"}
			case "wrong-repository":
				r.Owner.RepositoryCryptoID = string64("e")
			case "wrong-controller":
				r.Owner.ControllerID = "controller-ffffffffffff4fff8fffffffffffffff"
			case "foreign-snapshot":
				f.snapshots[1]["tags"] = []any{"checkpoint:" + testCheckpoint}
			case "missing-marker":
				f.snapshots = f.snapshots[1:]
			case "marker-target":
				r.SnapshotID = f.owner.OwnerSnapshotID
			}
			run := f.run
			if name == "bad-marker-body" {
				run = func(ctx context.Context, args ...string) ([]byte, error) {
					if args[0] == "dump" {
						return []byte(`{"schemaVersion":2,"controllerId":"other"}`), nil
					}
					return f.run(ctx, args...)
				}
			}
			if _, err := cleanupOwned(context.Background(), r, run); err == nil {
				t.Fatal("cleanup unexpectedly succeeded")
			}
			if len(f.mutations) != 0 {
				t.Fatal("mutated before ownership verification")
			}
		})
	}
	f := fakeOwned()
	f.failMutation = true
	if result, err := cleanupOwned(context.Background(), Request{Operation: "forget", SnapshotID: string64("c"), Owner: f.owner}, f.run); err == nil || result != nil {
		t.Fatal("partial failure claimed success")
	}
}
