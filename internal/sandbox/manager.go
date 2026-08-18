package sandbox

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"time"

	"github.com/chx739/sandboxd/internal/metrics"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	corelisters "k8s.io/client-go/listers/core/v1"
	"k8s.io/client-go/rest"
)

const readyCheckInterval = 100 * time.Millisecond

type Manager struct {
	client     kubernetes.Interface
	restConfig *rest.Config
	config     Config
	podLister  corelisters.PodLister
}

func NewManager(
	client kubernetes.Interface,
	restConfig *rest.Config,
	config Config,
	podLister corelisters.PodLister,
) *Manager {
	return &Manager{
		client:     client,
		restConfig: restConfig,
		config:     config,
		podLister:  podLister,
	}
}

// Create 创建一个已经被调用方占用的沙箱；预热池补充空闲 Pod 时使用 CreateIdle。
func (m *Manager) Create(ctx context.Context) (*Sandbox, error) {
	return m.create(ctx, StateBusy, "direct")
}

func (m *Manager) CreateIdle(ctx context.Context) (*Sandbox, error) {
	return m.create(ctx, StateIdle, "pool")
}

func (m *Manager) create(ctx context.Context, state State, source string) (*Sandbox, error) {
	started := time.Now()
	id, err := randomID()
	if err != nil {
		return nil, fmt.Errorf("生成沙箱 ID: %w", err)
	}

	pod := BuildPod(m.config, id)
	// 直接创建的沙箱必须从第一刻就是 busy，不能短暂暴露为可被池认领的 idle。
	pod.Labels[LabelState] = string(state)
	pod.Labels[LabelSource] = source

	created, err := m.client.CoreV1().Pods(m.config.Namespace).Create(ctx, pod, metav1.CreateOptions{})
	if err != nil {
		return nil, fmt.Errorf("创建 Pod %s: %w", pod.Name, err)
	}

	if err := m.WaitReady(ctx, created.Name); err != nil {
		// 原 ctx 可能已超时，清理必须使用独立短 context，否则 Delete 会立即失败。
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = m.deletePod(cleanupCtx, created.Name)
		return nil, fmt.Errorf("等待 Pod %s Ready: %w", created.Name, err)
	}

	if source == "direct" {
		metrics.AcquireDuration.WithLabelValues(source).Observe(time.Since(started).Seconds())
	}
	return &Sandbox{
		ID:        id,
		PodName:   created.Name,
		Namespace: created.Namespace,
		State:     state,
		CreatedAt: created.CreationTimestamp.Time,
		Source:    source,
	}, nil
}

// WaitReady 只轮询 informer 本地缓存，避免并发创建时反复请求 API Server。
// Running 不等于可用，必须看到 PodReady=True。
func (m *Manager) WaitReady(ctx context.Context, podName string) error {
	ticker := time.NewTicker(readyCheckInterval)
	defer ticker.Stop()

	for {
		pod, err := m.podLister.Pods(m.config.Namespace).Get(podName)
		switch {
		case err == nil:
			if isPodReady(pod) {
				return nil
			}
			if pod.Status.Phase == corev1.PodFailed || pod.Status.Phase == corev1.PodSucceeded {
				return fmt.Errorf("Pod 已终止，phase=%s reason=%s", pod.Status.Phase, pod.Status.Reason)
			}
		case apierrors.IsNotFound(err):
			// informer 事件可能还没到达本地缓存，下一次 ticker 再查。
		default:
			return fmt.Errorf("读取 informer 缓存: %w", err)
		}

		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
		}
	}
}

func (m *Manager) Delete(ctx context.Context, id string) error {
	return m.deletePod(ctx, "sandbox-"+id)
}

func (m *Manager) deletePod(ctx context.Context, podName string) error {
	propagation := metav1.DeletePropagationForeground
	err := m.client.CoreV1().Pods(m.config.Namespace).Delete(ctx, podName, metav1.DeleteOptions{
		PropagationPolicy: &propagation,
	})
	if apierrors.IsNotFound(err) {
		return nil
	}
	if err != nil {
		return fmt.Errorf("删除 Pod %s: %w", podName, err)
	}
	return nil
}

func isPodReady(pod *corev1.Pod) bool {
	for _, condition := range pod.Status.Conditions {
		if condition.Type == corev1.PodReady && condition.Status == corev1.ConditionTrue {
			return true
		}
	}
	return false
}

func randomID() (string, error) {
	bytes := make([]byte, 8)
	if _, err := rand.Read(bytes); err != nil {
		return "", err
	}
	return hex.EncodeToString(bytes), nil
}
