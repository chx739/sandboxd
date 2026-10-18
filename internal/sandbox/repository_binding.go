package sandbox

import "github.com/chx739/sandboxd/internal/backuphelper"

// RepositoryBinding固定控制日志所管理的唯一仓库。它不是认证令牌；
// Secret的实际内容仍由部署者负责，helper另外核验真实crypto ID与归属标记。
type RepositoryBinding struct {
	Version    int                          `json:"schemaVersion"`
	Owner      backuphelper.RepositoryOwner `json:"owner"`
	SecretName string                       `json:"secretName"`
	EndpointIP string                       `json:"endpointIp"`
}

func bindingFor(job BackupJobConfig) RepositoryBinding {
	return RepositoryBinding{Version: 1, Owner: job.Owner, SecretName: job.SecretName, EndpointIP: job.EndpointIP}
}

func (r *WorkspaceRegistry) validateRepositoryBinding(binding RepositoryBinding) error {
	if binding.Version != 1 || !binding.Owner.Valid() || r.repository != (RepositoryBinding{}) || len(r.records) != 0 || len(r.barriers) != 0 {
		return ErrWorkspaceConflict
	}
	if _, err := BuildBackupNetworkPolicy(binding.EndpointIP); err != nil {
		return err
	}
	// 复用已有Secret/Pod配置校验，镜像不绑定在仓库上；每次执行仍由Job固定镜像。
	if _, err := BuildBackupPod(Config{Image: "binding-validation", RuntimeClass: "gvisor"}, "validate", "workspace-11111111111111111111111111111111", binding.SecretName, backuphelper.Request{Operation: "config"}); err != nil {
		return err
	}
	return nil
}

func (r *WorkspaceRegistry) repositoryMatches(job BackupJobConfig) bool {
	if r.repository == (RepositoryBinding{}) {
		return job.Owner == (backuphelper.RepositoryOwner{})
	}
	return bindingFor(job) == r.repository
}

// 只能在全新控制日志中首次登记；旧共享仓库/工作区绝不自动收编。
// 相同配置重启不追加事件，遗漏Owner同样是拒绝降级。
func (r *WorkspaceRegistry) bindRepository(job BackupJobConfig) error {
	r.mu.Lock()
	defer r.mu.Unlock()
	if r.repository != (RepositoryBinding{}) || job.Owner == (backuphelper.RepositoryOwner{}) {
		if !r.repositoryMatches(job) {
			return ErrWorkspaceConflict
		}
		return nil
	}
	binding := bindingFor(job)
	if err := r.validateRepositoryBinding(binding); err != nil {
		return err
	}
	if len(r.journal.Entries()) != 0 {
		return ErrWorkspaceConflict
	}
	if _, err := r.journal.Append("repository.binding", binding); err != nil {
		return err
	}
	r.repository = binding
	return nil
}
