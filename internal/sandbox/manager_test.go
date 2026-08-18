package sandbox

import (
	"context"
	"errors"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	corelisters "k8s.io/client-go/listers/core/v1"
	"k8s.io/client-go/tools/cache"
)

func TestWaitReadyRequiresReadyCondition(t *testing.T) {
	indexer := cache.NewIndexer(cache.MetaNamespaceKeyFunc, cache.Indexers{
		cache.NamespaceIndex: cache.MetaNamespaceIndexFunc,
	})
	lister := corelisters.NewPodLister(indexer)
	manager := &Manager{config: Config{Namespace: "sandboxd-demo"}, podLister: lister}

	runningOnly := &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "sandbox-running", Namespace: "sandboxd-demo"},
		Status:     corev1.PodStatus{Phase: corev1.PodRunning},
	}
	if err := indexer.Add(runningOnly); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Millisecond)
	defer cancel()
	if err := manager.WaitReady(ctx, runningOnly.Name); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("只有 Running 不应视为 Ready，得到错误：%v", err)
	}

	ready := runningOnly.DeepCopy()
	ready.Status.Conditions = []corev1.PodCondition{{
		Type:   corev1.PodReady,
		Status: corev1.ConditionTrue,
	}}
	if err := indexer.Update(ready); err != nil {
		t.Fatal(err)
	}
	if err := manager.WaitReady(context.Background(), ready.Name); err != nil {
		t.Fatalf("PodReady=True 应立即通过：%v", err)
	}
}

func TestRandomIDIsDNSLabelFriendly(t *testing.T) {
	first, err := randomID()
	if err != nil {
		t.Fatal(err)
	}
	second, err := randomID()
	if err != nil {
		t.Fatal(err)
	}
	if len(first) != 16 || first == second {
		t.Fatalf("随机 ID 不符合预期：first=%q second=%q", first, second)
	}
}
