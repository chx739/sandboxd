package sandbox

import (
	"context"
	"sync"
	"testing"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes/fake"
	corelisters "k8s.io/client-go/listers/core/v1"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/util/workqueue"
)

func TestConcurrentClaimReturnsDifferentPods(t *testing.T) {
	first := readyIdlePod("first")
	second := readyIdlePod("second")
	client := fake.NewSimpleClientset(first.DeepCopy(), second.DeepCopy())
	indexer := cache.NewIndexer(cache.MetaNamespaceKeyFunc, cache.Indexers{
		cache.NamespaceIndex: cache.MetaNamespaceIndexFunc,
	})
	if err := indexer.Add(first); err != nil {
		t.Fatal(err)
	}
	if err := indexer.Add(second); err != nil {
		t.Fatal(err)
	}
	lister := corelisters.NewPodLister(indexer)
	manager := &Manager{client: client, config: Config{Namespace: "sandboxd-demo"}, podLister: lister}
	pool := &Pool{
		client:  client,
		lister:  lister,
		manager: manager,
		queue: workqueue.NewTypedRateLimitingQueue(
			workqueue.DefaultTypedControllerRateLimiter[string](),
		),
	}
	defer pool.queue.ShutDown()

	results := make(chan string, 2)
	errors := make(chan error, 2)
	var wait sync.WaitGroup
	for range 2 {
		wait.Add(1)
		go func() {
			defer wait.Done()
			claimed, err := pool.Claim(context.Background())
			if err != nil {
				errors <- err
				return
			}
			results <- claimed.ID
		}()
	}
	wait.Wait()
	close(results)
	close(errors)

	for err := range errors {
		t.Fatalf("并发 Claim 失败：%v", err)
	}
	seen := map[string]bool{}
	for id := range results {
		if seen[id] {
			t.Fatalf("重复认领同一个 ID：%s", id)
		}
		seen[id] = true
	}
	if len(seen) != 2 {
		t.Fatalf("认领数量=%d，期望 2", len(seen))
	}
}

func readyIdlePod(id string) *corev1.Pod {
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "sandbox-" + id,
			Namespace: "sandboxd-demo",
			Labels: map[string]string{
				LabelManagedBy: ValueManagedBy,
				LabelState:     string(StateIdle),
				LabelID:        id,
			},
		},
		Status: corev1.PodStatus{
			Phase: corev1.PodRunning,
			Conditions: []corev1.PodCondition{{
				Type:   corev1.PodReady,
				Status: corev1.ConditionTrue,
			}},
		},
	}
}
