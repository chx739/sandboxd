package approval

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"sync"
	"time"

	"github.com/chx739/sandboxd/internal/metrics"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/util/validation"
	"k8s.io/client-go/kubernetes"
)

var deniedNamespaces = map[string]struct{}{
	"default":         {},
	"kube-system":     {},
	"kube-public":     {},
	"kube-node-lease": {},
	"sandboxd-demo":   {},
}

// Service 是单进程内存 Plan Store，同时封装 DryRun 和最终执行。
// Demo 重启后 Plan 丢失是明确边界；生产实现应使用持久化存储和审计日志。
type Service struct {
	client kubernetes.Interface

	mu    sync.RWMutex
	plans map[string]*Plan
	order []string
}

func NewService(client kubernetes.Interface) *Service {
	return &Service{
		client: client,
		plans:  make(map[string]*Plan),
	}
}

func (s *Service) Propose(ctx context.Context, input ProposeInput) (*Plan, error) {
	if _, denied := deniedNamespaces[input.Namespace]; denied || input.Namespace == "" {
		metrics.PlanDenied.WithLabelValues("namespace").Inc()
		return nil, ErrNamespaceDenied
	}
	if input.Replicas < 0 || input.Replicas > MaxReplicas {
		metrics.PlanDenied.WithLabelValues("replicas").Inc()
		return nil, ErrReplicasDenied
	}
	if len(validation.IsDNS1123Label(input.Namespace)) > 0 ||
		len(validation.IsDNS1123Subdomain(input.Name)) > 0 || input.Name == "" {
		metrics.PlanDenied.WithLabelValues("target").Inc()
		return nil, ErrTargetInvalid
	}

	deployment, err := s.client.AppsV1().Deployments(input.Namespace).Get(ctx, input.Name, metav1.GetOptions{})
	if err != nil {
		return nil, fmt.Errorf("读取 Deployment: %w", err)
	}
	before := int32(1)
	if deployment.Spec.Replicas != nil {
		before = *deployment.Spec.Replicas
	}

	// server-side dry-run 会经过认证、授权、默认值和准入校验，但不会持久化。
	candidate := deployment.DeepCopy()
	candidate.Spec.Replicas = int32Pointer(input.Replicas)
	if _, err := s.client.AppsV1().Deployments(input.Namespace).Update(
		ctx,
		candidate,
		metav1.UpdateOptions{DryRun: []string{metav1.DryRunAll}},
	); err != nil {
		return nil, fmt.Errorf("Deployment scale server-side dry-run: %w", err)
	}

	id, err := randomID()
	if err != nil {
		return nil, fmt.Errorf("生成 Plan ID: %w", err)
	}
	now := time.Now().UTC()
	plan := &Plan{
		ID:                    id,
		Action:                "deployment.scale",
		Namespace:             input.Namespace,
		Name:                  input.Name,
		BeforeReplicas:        before,
		AfterReplicas:         input.Replicas,
		TargetUID:             string(deployment.UID),
		TargetResourceVersion: deployment.ResourceVersion,
		Status:                StatusPending,
		DryRunValidated:       true,
		CreatedAt:             now,
	}

	s.mu.Lock()
	s.plans[id] = plan
	s.order = append(s.order, id)
	s.mu.Unlock()
	return clonePlan(plan), nil
}

func (s *Service) List() []*Plan {
	s.mu.RLock()
	defer s.mu.RUnlock()

	items := make([]*Plan, 0, len(s.order))
	for index := len(s.order) - 1; index >= 0; index-- {
		items = append(items, clonePlan(s.plans[s.order[index]]))
	}
	return items
}

func (s *Service) Approve(ctx context.Context, id string) (*Plan, error) {
	plan, err := s.beginExecution(id)
	if err != nil {
		return nil, err
	}

	current, err := s.client.AppsV1().Deployments(plan.Namespace).Get(ctx, plan.Name, metav1.GetOptions{})
	if err != nil {
		if apierrors.IsNotFound(err) {
			return s.markStale(id)
		}
		s.restorePending(id)
		return nil, fmt.Errorf("批准前读取 Deployment: %w", err)
	}
	if string(current.UID) != plan.TargetUID || current.ResourceVersion != plan.TargetResourceVersion {
		return s.markStale(id)
	}

	current.Spec.Replicas = int32Pointer(plan.AfterReplicas)
	if _, err := s.client.AppsV1().Deployments(plan.Namespace).Update(ctx, current, metav1.UpdateOptions{}); err != nil {
		if apierrors.IsConflict(err) || apierrors.IsNotFound(err) {
			return s.markStale(id)
		}
		s.restorePending(id)
		return nil, fmt.Errorf("执行 Deployment scale: %w", err)
	}

	now := time.Now().UTC()
	s.mu.Lock()
	stored := s.plans[id]
	stored.Status = StatusApproved
	stored.DecidedAt = &now
	result := clonePlan(stored)
	s.mu.Unlock()
	return result, nil
}

func (s *Service) Reject(id string) (*Plan, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	plan, ok := s.plans[id]
	if !ok {
		return nil, ErrPlanNotFound
	}
	if plan.Status != StatusPending {
		metrics.PlanDenied.WithLabelValues("state").Inc()
		return nil, ErrPlanState
	}
	now := time.Now().UTC()
	plan.Status = StatusRejected
	plan.DecidedAt = &now
	return clonePlan(plan), nil
}

func (s *Service) beginExecution(id string) (*Plan, error) {
	s.mu.Lock()
	defer s.mu.Unlock()

	plan, ok := s.plans[id]
	if !ok {
		return nil, ErrPlanNotFound
	}
	if plan.Status != StatusPending {
		metrics.PlanDenied.WithLabelValues("state").Inc()
		return nil, ErrPlanState
	}
	// 先抢占审批权；并发的第二次 approve 会看到 executing 并被拒绝。
	plan.Status = StatusExecuting
	return clonePlan(plan), nil
}

func (s *Service) markStale(id string) (*Plan, error) {
	metrics.PlanDenied.WithLabelValues("changed").Inc()
	now := time.Now().UTC()
	s.mu.Lock()
	plan := s.plans[id]
	plan.Status = StatusStale
	plan.DecidedAt = &now
	result := clonePlan(plan)
	s.mu.Unlock()
	return result, ErrTargetChanged
}

func (s *Service) restorePending(id string) {
	s.mu.Lock()
	if plan, ok := s.plans[id]; ok && plan.Status == StatusExecuting {
		plan.Status = StatusPending
	}
	s.mu.Unlock()
}

func clonePlan(plan *Plan) *Plan {
	copy := *plan
	if plan.DecidedAt != nil {
		decidedAt := *plan.DecidedAt
		copy.DecidedAt = &decidedAt
	}
	return &copy
}

func int32Pointer(value int32) *int32 {
	return &value
}

func randomID() (string, error) {
	value := make([]byte, 8)
	if _, err := rand.Read(value); err != nil {
		return "", err
	}
	return hex.EncodeToString(value), nil
}
