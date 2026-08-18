package sandbox

import (
	corev1 "k8s.io/api/core/v1"

	"github.com/chx739/sandboxd/internal/metrics"
)

func recordPoolSize(pods []*corev1.Pod) {
	counts := map[State]int{StateIdle: 0, StateBusy: 0}
	for _, pod := range pods {
		if pod.DeletionTimestamp != nil || pod.Status.Phase == corev1.PodFailed || pod.Status.Phase == corev1.PodSucceeded {
			continue
		}
		state := State(pod.Labels[LabelState])
		if state == StateIdle || state == StateBusy {
			counts[state]++
		}
	}
	metrics.PoolSize.WithLabelValues(string(StateIdle)).Set(float64(counts[StateIdle]))
	metrics.PoolSize.WithLabelValues(string(StateBusy)).Set(float64(counts[StateBusy]))
}
