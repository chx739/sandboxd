package sandbox

import (
	"errors"
	"fmt"
	"sort"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/labels"
)

const LabelSource = "sandbox.io/source"

var ErrWorkspaceExecDenied = errors.New("persistent workspace Pods require the restricted file interface")

func isPersistentWorkspace(pod *corev1.Pod) bool {
	return pod.Labels[LabelWorkspace] != "" || pod.Labels[LabelSource] == "workspace"
}

// GenericExecPodName preserves the legacy stateless demo endpoint while closing
// that bypass for persistent files. Diagnostics keep their fixed command route.
func (m *Manager) GenericExecPodName(id string) (string, error) {
	name, err := m.PodName(id)
	if err != nil {
		return "", err
	}
	pod, err := m.podLister.Pods(m.config.Namespace).Get(name)
	if err != nil {
		return "", err
	}
	if isPersistentWorkspace(pod) {
		return "", ErrWorkspaceExecDenied
	}
	return name, nil
}

func (m *Manager) List() ([]Sandbox, error) {
	pods, err := m.podLister.Pods(m.config.Namespace).List(labels.SelectorFromSet(labels.Set{
		LabelManagedBy: ValueManagedBy,
	}))
	if err != nil {
		return nil, fmt.Errorf("从 informer 缓存列出 Pod: %w", err)
	}

	result := make([]Sandbox, 0, len(pods))
	for _, pod := range pods {
		result = append(result, Sandbox{
			ID:        pod.Labels[LabelID],
			PodName:   pod.Name,
			Namespace: pod.Namespace,
			State:     State(pod.Labels[LabelState]),
			CreatedAt: pod.CreationTimestamp.Time,
			Source:    pod.Labels[LabelSource],
		})
	}
	sort.Slice(result, func(i, j int) bool {
		return result[i].CreatedAt.Before(result[j].CreatedAt)
	})
	return result, nil
}

func (m *Manager) PodName(id string) (string, error) {
	podName := "sandbox-" + id
	pod, err := m.podLister.Pods(m.config.Namespace).Get(podName)
	if err != nil {
		return "", fmt.Errorf("从 informer 缓存获取 Pod %s: %w", podName, err)
	}
	if pod.Labels[LabelManagedBy] != ValueManagedBy || pod.Labels[LabelID] != id {
		return "", fmt.Errorf("Pod %s 不属于 sandboxd", podName)
	}
	return pod.Name, nil
}
