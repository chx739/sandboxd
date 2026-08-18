package sandbox

import "time"

type State string

const (
	StateIdle     State = "idle"
	StateBusy     State = "busy"
	StateDraining State = "draining"
)

// label key 集中定义，避免 Manager、Pool 和 API 各自拼写字符串。
const (
	LabelManagedBy = "sandbox.io/managed-by"
	LabelState     = "sandbox.io/state"
	LabelID        = "sandbox.io/id"
	ValueManagedBy = "sandboxd"
)

const ServiceAccountName = "sandbox-reader"

// Config 只放构造 Pod 必需的输入；默认值和参数解析留给 config 包负责。
type Config struct {
	Namespace    string
	Image        string
	RuntimeClass string
}

type Sandbox struct {
	ID        string    `json:"id"`
	PodName   string    `json:"podName"`
	Namespace string    `json:"namespace"`
	State     State     `json:"state"`
	CreatedAt time.Time `json:"createdAt"`
	Source    string    `json:"source"`
}
