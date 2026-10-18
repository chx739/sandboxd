package sandbox

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"sort"
	"strings"
	"sync"

	"github.com/chx739/sandboxd/internal/control"
)

var ErrWorkspaceConflict = errors.New("workspace ownership conflict")

type WorkspaceOwner struct {
	TaskID          string `json:"taskId"`
	AttemptID       string `json:"attemptId"`
	SandboxID       string `json:"sandboxId"`
	PodName         string `json:"podName"`
	PodUID          string `json:"podUid,omitempty"`
	NodeName        string `json:"nodeName,omitempty"`
	CreateSubmitted bool   `json:"createSubmitted"`
	StopConfirmed   bool   `json:"stopConfirmed"`
}

// WorkspaceRecord is Go's authority. Labels are checked against this record,
// never used as a replacement for it. Every update is fsynced before publication.
type WorkspaceRecord struct {
	Version      int            `json:"schemaVersion"`
	WorkspaceID  string         `json:"workspaceId"`
	SessionID    string         `json:"sessionId"`
	BranchID     string         `json:"branchId"`
	Generation   uint64         `json:"generation"`
	Phase        string         `json:"phase"`
	Owner        WorkspaceOwner `json:"owner"`
	ImageRef     string         `json:"imageRef,omitempty"`
	ImageID      string         `json:"imageId,omitempty"`
	StopReason   string         `json:"stopReason,omitempty"`
	RestoredFrom RestoreSource  `json:"restoredFrom,omitzero"`
}

type WorkspaceAccess struct {
	WorkspaceID string `json:"workspaceId"`
	TaskID      string `json:"taskId"`
	AttemptID   string `json:"attemptId"`
	SandboxID   string `json:"sandboxId"`
	Generation  uint64 `json:"generation"`
}

func (a WorkspaceAccess) Validate() error {
	if err := (WorkspaceMount{WorkspaceID: a.WorkspaceID, TaskID: a.TaskID, AttemptID: a.AttemptID, Generation: a.Generation}).Validate(); err != nil {
		return err
	}
	if !runtimeIDPattern.MatchString(a.SandboxID) {
		return errors.New("invalid workspace sandbox identity")
	}
	return nil
}

type WorkspaceRegistry struct {
	mu           sync.Mutex
	journal      *control.Journal
	records      map[string]WorkspaceRecord
	barriers     map[string]WorkspaceBarrier
	repository   RepositoryBinding
	cleanupPlans map[string]cleanupPlan
	cleanupJobs  map[string]bool
}

func OpenWorkspaceRegistry(path string) (*WorkspaceRegistry, error) {
	journal, err := control.OpenJournal(path)
	if err != nil {
		return nil, err
	}
	r := &WorkspaceRegistry{journal: journal, records: map[string]WorkspaceRecord{}, barriers: map[string]WorkspaceBarrier{}, cleanupPlans: map[string]cleanupPlan{}, cleanupJobs: map[string]bool{}}
	for _, entry := range journal.Entries() {
		if entry.Type == "repository.binding" {
			var binding RepositoryBinding
			decoder := json.NewDecoder(strings.NewReader(string(entry.Data)))
			decoder.DisallowUnknownFields()
			if decoder.Decode(&binding) != nil || r.validateRepositoryBinding(binding) != nil {
				journal.Close()
				return nil, errors.New("invalid persistent repository binding")
			}
			r.repository = binding
			continue
		}
		if entry.Type == "workspace.barrier" {
			var barrier WorkspaceBarrier
			decoder := json.NewDecoder(strings.NewReader(string(entry.Data)))
			decoder.DisallowUnknownFields()
			if decoder.Decode(&barrier) != nil || r.validateBarrier(barrier) != nil {
				journal.Close()
				return nil, errors.New("invalid persistent workspace barrier")
			}
			r.barriers[barrier.WorkspaceID] = barrier
			r.indexCleanup(barrier)
			continue
		}
		var record WorkspaceRecord
		decoder := json.NewDecoder(strings.NewReader(string(entry.Data)))
		decoder.DisallowUnknownFields()
		if entry.Type != "workspace.state" || decoder.Decode(&record) != nil {
			journal.Close()
			return nil, errors.New("invalid workspace control record")
		}
		if err := r.validate(record); err != nil {
			journal.Close()
			return nil, err
		}
		r.records[record.WorkspaceID] = record
	}
	return r, nil
}

func (r *WorkspaceRegistry) validate(record WorkspaceRecord) error {
	if r.repository != (RepositoryBinding{}) && record.WorkspaceID == r.cleanupWorkspaceID() {
		return ErrWorkspaceConflict // 维护关联ID不是可挂载的业务工作区。
	}
	if record.RestoredFrom != (RestoreSource{}) && !record.RestoredFrom.valid() {
		return ErrWorkspaceConflict
	}
	if record.Version != 1 || !workspaceIDPattern.MatchString(record.WorkspaceID) || !runtimeIDPattern.MatchString(record.SessionID) || !strings.HasPrefix(record.SessionID, "session-") || !runtimeIDPattern.MatchString(record.BranchID) || !strings.HasPrefix(record.BranchID, "branch-") {
		return errors.New("invalid workspace record identity")
	}
	switch record.Phase {
	case "registered", "ready":
		if record.Generation != 0 || record.Owner != (WorkspaceOwner{}) {
			return errors.New("unclaimed workspace contains an owner")
		}
	case "starting", "bound", "stopping", "released":
		mount := WorkspaceMount{WorkspaceID: record.WorkspaceID, TaskID: record.Owner.TaskID, AttemptID: record.Owner.AttemptID, Generation: record.Generation}
		if mount.Validate() != nil || !runtimeIDPattern.MatchString(record.Owner.SandboxID) || len(record.Owner.PodName) > 63 || record.Owner.PodName != "sandbox-"+record.Owner.SandboxID || record.ImageRef == "" || len(record.ImageRef) > 512 {
			return errors.New("invalid persistent workspace owner")
		}
		if record.Phase == "bound" && (record.Owner.PodUID == "" || record.Owner.NodeName == "" || record.ImageID == "" || !record.Owner.CreateSubmitted || record.Owner.StopConfirmed) {
			return errors.New("incomplete workspace binding")
		}
		if record.Phase == "released" && !record.Owner.StopConfirmed {
			return errors.New("released workspace has an uncertain writer")
		}
	default:
		return errors.New("invalid workspace phase")
	}
	if previous, ok := r.records[record.WorkspaceID]; ok {
		if record.RestoredFrom != previous.RestoredFrom {
			b, exists := r.barriers[record.WorkspaceID]
			if previous.RestoredFrom != (RestoreSource{}) || !exists || b.Operation != "restore" || b.Phase != "stopped" || b.StopReason != "containers-terminated" || !b.RestoreVerified || b.RestoreSource != record.RestoredFrom || record.Phase != "ready" || record.Generation != 0 {
				return ErrWorkspaceConflict
			}
		}
		if record.SessionID != previous.SessionID || record.BranchID != previous.BranchID || record.Generation < previous.Generation || record.Generation > previous.Generation+1 {
			return errors.New("workspace scope or generation changed unexpectedly")
		}
		if record.Generation == previous.Generation && record.Owner.AttemptID != previous.Owner.AttemptID {
			return errors.New("workspace owner changed without a generation")
		}
		if record.Generation == previous.Generation {
			if record.Owner.TaskID != previous.Owner.TaskID || record.Owner.SandboxID != previous.Owner.SandboxID || record.Owner.PodName != previous.Owner.PodName || record.ImageRef != previous.ImageRef || (previous.Owner.CreateSubmitted && !record.Owner.CreateSubmitted) || (previous.Owner.StopConfirmed && !record.Owner.StopConfirmed) {
				return errors.New("workspace owner history moved backwards")
			}
			if (previous.Owner.PodUID != "" && record.Owner.PodUID != previous.Owner.PodUID) || (previous.ImageID != "" && record.ImageID != previous.ImageID) {
				return errors.New("workspace runtime identity changed within an attempt")
			}
			allowed := map[string]map[string]bool{
				"registered": {"registered": true, "ready": true}, "ready": {"ready": true},
				"starting": {"starting": true, "bound": true, "stopping": true}, "bound": {"stopping": true},
				"stopping": {"stopping": true, "released": true}, "released": {"released": true},
			}
			if !allowed[previous.Phase][record.Phase] {
				return errors.New("invalid workspace lifecycle transition")
			}
		}
		if record.Generation > previous.Generation && previous.Phase != "ready" && previous.Phase != "released" {
			return errors.New("workspace takeover without a stopped owner")
		}
		if r.barrierActive(record.WorkspaceID) && record.Generation != previous.Generation {
			return ErrWorkspaceConflict
		}
	} else if record.Phase != "registered" || record.Generation != 0 || record.RestoredFrom != (RestoreSource{}) {
		return errors.New("workspace history must start with registration")
	}
	return nil
}

func (r *WorkspaceRegistry) save(record WorkspaceRecord) (WorkspaceRecord, error) {
	if err := r.validate(record); err != nil {
		return WorkspaceRecord{}, err
	}
	if _, err := r.journal.Append("workspace.state", record); err != nil {
		return WorkspaceRecord{}, err
	}
	r.records[record.WorkspaceID] = record
	return record, nil
}

func (r *WorkspaceRegistry) Register(workspaceID, sessionID, branchID string) (WorkspaceRecord, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if record, ok := r.records[workspaceID]; ok {
		if record.SessionID == sessionID && record.BranchID == branchID {
			return record, nil
		}
		return WorkspaceRecord{}, ErrWorkspaceConflict
	}
	if len(r.records) >= 256 {
		return WorkspaceRecord{}, errors.New("workspace registry limit reached")
	}
	return r.save(WorkspaceRecord{Version: 1, WorkspaceID: workspaceID, SessionID: sessionID, BranchID: branchID, Phase: "registered"})
}

func (r *WorkspaceRegistry) Ready(workspaceID string) (WorkspaceRecord, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	record, ok := r.records[workspaceID]
	if !ok {
		return WorkspaceRecord{}, ErrWorkspaceConflict
	}
	if record.Phase == "ready" {
		return record, nil
	}
	if record.Phase != "registered" {
		return WorkspaceRecord{}, ErrWorkspaceConflict
	}
	record.Phase = "ready"
	return r.save(record)
}

func (r *WorkspaceRegistry) Get(workspaceID string) (WorkspaceRecord, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	record, ok := r.records[workspaceID]
	if !ok {
		return WorkspaceRecord{}, ErrWorkspaceConflict
	}
	return record, nil
}

func (r *WorkspaceRegistry) Records() []WorkspaceRecord {
	r.mu.Lock()
	defer r.mu.Unlock()
	result := make([]WorkspaceRecord, 0, len(r.records))
	for _, record := range r.records {
		result = append(result, record)
	}
	sort.Slice(result, func(i, j int) bool { return result[i].WorkspaceID < result[j].WorkspaceID })
	return result
}

func (r *WorkspaceRegistry) BeginClaim(workspaceID, taskID, attemptID, sandboxID, imageRef string) (WorkspaceRecord, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.barrierActive(workspaceID) {
		return WorkspaceRecord{}, ErrWorkspaceConflict
	}
	record, ok := r.records[workspaceID]
	if !ok {
		return WorkspaceRecord{}, ErrWorkspaceConflict
	}
	if (record.Phase == "starting" || record.Phase == "bound") && record.Owner.TaskID == taskID && record.Owner.AttemptID == attemptID {
		if record.ImageRef != imageRef {
			return WorkspaceRecord{}, ErrWorkspaceConflict
		}
		return record, nil
	}
	if record.Phase != "ready" && record.Phase != "released" {
		return WorkspaceRecord{}, ErrWorkspaceConflict
	}
	if record.Owner.AttemptID == attemptID {
		return WorkspaceRecord{}, errors.New("released attempt cannot be resurrected")
	}
	if record.Owner.TaskID == taskID && record.ImageRef != imageRef {
		return WorkspaceRecord{}, errors.New("automatic recovery cannot change the task image")
	}
	expectedImageID := record.ImageID
	if record.Owner.TaskID != taskID {
		expectedImageID = ""
	}
	if record.RestoredFrom != (RestoreSource{}) {
		if record.RestoredFrom.ImageRef != imageRef {
			return WorkspaceRecord{}, ErrWorkspaceConflict
		}
		expectedImageID = record.RestoredFrom.ImageID
	}
	record.Generation++
	record.Phase = "starting"
	record.Owner = WorkspaceOwner{TaskID: taskID, AttemptID: attemptID, SandboxID: sandboxID, PodName: "sandbox-" + sandboxID}
	record.ImageRef = imageRef
	record.ImageID = expectedImageID
	record.StopReason = ""
	return r.save(record)
}

func matches(record WorkspaceRecord, access WorkspaceAccess) bool {
	return record.WorkspaceID == access.WorkspaceID && record.Generation == access.Generation && record.Owner.TaskID == access.TaskID && record.Owner.AttemptID == access.AttemptID && record.Owner.SandboxID == access.SandboxID
}

func (r *WorkspaceRegistry) update(access WorkspaceAccess, fn func(*WorkspaceRecord) error) (WorkspaceRecord, error) {
	r.mu.Lock()
	defer r.mu.Unlock()
	record, ok := r.records[access.WorkspaceID]
	if !ok || !matches(record, access) {
		return WorkspaceRecord{}, ErrWorkspaceConflict
	}
	if err := fn(&record); err != nil {
		return WorkspaceRecord{}, err
	}
	return r.save(record)
}

func (r *WorkspaceRegistry) CreationSubmitted(access WorkspaceAccess) (WorkspaceRecord, error) {
	return r.update(access, func(record *WorkspaceRecord) error {
		if record.Phase != "starting" || record.Owner.StopConfirmed {
			return ErrWorkspaceConflict
		}
		record.Owner.CreateSubmitted = true
		return nil
	})
}

func (r *WorkspaceRegistry) Bind(access WorkspaceAccess, podUID, nodeName, imageID string) (WorkspaceRecord, error) {
	return r.update(access, func(record *WorkspaceRecord) error {
		if record.Phase != "starting" || !record.Owner.CreateSubmitted || !runtimeIDPattern.MatchString(podUID) || !runtimeIDPattern.MatchString(nodeName) || len(imageID) > 512 || imageID == "" || (record.ImageID != "" && record.ImageID != imageID) {
			return ErrWorkspaceConflict
		}
		record.Owner.PodUID = podUID
		record.Owner.NodeName = nodeName
		record.ImageID = imageID
		record.Phase = "bound"
		return nil
	})
}

// Identify 在 Ready 之前也保存 API 已确认的 UID；创建/挂载中断后才能精确清理。
func (r *WorkspaceRegistry) Identify(access WorkspaceAccess, podUID, nodeName string) (WorkspaceRecord, error) {
	return r.update(access, func(record *WorkspaceRecord) error {
		if record.Phase != "starting" || !record.Owner.CreateSubmitted || !runtimeIDPattern.MatchString(podUID) || (nodeName != "" && !runtimeIDPattern.MatchString(nodeName)) {
			return ErrWorkspaceConflict
		}
		if record.Owner.PodUID != "" && record.Owner.PodUID != podUID {
			return ErrWorkspaceConflict
		}
		record.Owner.PodUID = podUID
		if nodeName != "" {
			record.Owner.NodeName = nodeName
		}
		return nil
	})
}

func (r *WorkspaceRegistry) StartStop(access WorkspaceAccess) (WorkspaceRecord, error) {
	return r.update(access, func(record *WorkspaceRecord) error {
		if record.Phase != "starting" && record.Phase != "bound" && record.Phase != "stopping" {
			return ErrWorkspaceConflict
		}
		record.Phase = "stopping"
		return nil
	})
}

// ConfirmStopped is called by trusted Kubernetes reconciliation, not an HTTP
// request carrying a caller-supplied "stopped=true". A deleted object, expired
// lease or larger generation is deliberately not a stop proof.
func (r *WorkspaceRegistry) ConfirmStopped(access WorkspaceAccess, podUID, reason string) (WorkspaceRecord, error) {
	return r.update(access, func(record *WorkspaceRecord) error {
		if record.Phase != "stopping" {
			return ErrWorkspaceConflict
		}
		switch reason {
		case "never-submitted":
			if record.Owner.CreateSubmitted || podUID != "" {
				return ErrWorkspaceConflict
			}
		case "containers-terminated":
			if podUID == "" || (record.Owner.PodUID != "" && record.Owner.PodUID != podUID) {
				return ErrWorkspaceConflict
			}
			record.Owner.PodUID = podUID
		default:
			return errors.New("unsupported workspace stop evidence")
		}
		record.Owner.StopConfirmed = true
		record.StopReason = reason
		return nil
	})
}

func (r *WorkspaceRegistry) Released(access WorkspaceAccess) (WorkspaceRecord, error) {
	return r.update(access, func(record *WorkspaceRecord) error {
		if record.Phase == "released" {
			return nil
		}
		if record.Phase != "stopping" || !record.Owner.StopConfirmed {
			return ErrWorkspaceConflict
		}
		record.Phase = "released"
		return nil
	})
}

// WithOwner 把一次文件 RPC 和归属切换串行化，防止“校验后刚好换 owner”。
// 当前只有一个 Agent Worker，先用全局锁保证正确；不据此宣传并发性能。
func (r *WorkspaceRegistry) WithOwner(ctx context.Context, access WorkspaceAccess, fn func(WorkspaceRecord) error) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if err := ctx.Err(); err != nil {
		return err
	}
	record, ok := r.records[access.WorkspaceID]
	if !ok || record.Phase != "bound" || !matches(record, access) || record.Owner.StopConfirmed || r.barrierActive(access.WorkspaceID) {
		return ErrWorkspaceConflict
	}
	if err := fn(record); err != nil {
		return fmt.Errorf("workspace operation: %w", err)
	}
	return nil
}

func (r *WorkspaceRegistry) Close() error { return r.journal.Close() }
