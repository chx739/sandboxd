package approval

import (
	"errors"
	"time"
)

const MaxReplicas int32 = 10

type Status string

const (
	StatusPending   Status = "pending"
	StatusExecuting Status = "executing"
	StatusApproved  Status = "approved"
	StatusRejected  Status = "rejected"
	StatusStale     Status = "stale"
)

var (
	ErrNamespaceDenied = errors.New("namespace 不允许写入")
	ErrReplicasDenied  = errors.New("replicas 必须在 0 到 10 之间")
	ErrTargetInvalid   = errors.New("Deployment 目标格式不合法")
	ErrPlanNotFound    = errors.New("Plan 不存在")
	ErrPlanState       = errors.New("Plan 当前状态不允许该操作")
	ErrTargetChanged   = errors.New("Deployment 在审批期间已变化，请重新提交 Plan")
)

type ProposeInput struct {
	Namespace string `json:"namespace"`
	Name      string `json:"name"`
	Replicas  int32  `json:"replicas"`
}

// Plan 只描述一种动作：Deployment scale。固定动作比“任意 YAML”更容易审计和限权。
type Plan struct {
	ID                    string     `json:"id"`
	Action                string     `json:"action"`
	Namespace             string     `json:"namespace"`
	Name                  string     `json:"name"`
	BeforeReplicas        int32      `json:"beforeReplicas"`
	AfterReplicas         int32      `json:"afterReplicas"`
	TargetUID             string     `json:"targetUID"`
	TargetResourceVersion string     `json:"targetResourceVersion"`
	Status                Status     `json:"status"`
	DryRunValidated       bool       `json:"dryRunValidated"`
	CreatedAt             time.Time  `json:"createdAt"`
	DecidedAt             *time.Time `json:"decidedAt,omitempty"`
}
