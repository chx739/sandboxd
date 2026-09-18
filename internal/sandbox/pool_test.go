package sandbox

import (
	"bytes"
	"context"
	"errors"
	"strings"
	"sync"
	"testing"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/util/validation/field"
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
		client:        client,
		lister:        lister,
		manager:       manager,
		createTimeout: time.Minute,
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

func TestReconcileCreateIdleIsBounded(t *testing.T) {
	// 场景：Pod 创建后永远停在 Pending（fake informer 不推进状态，
	// 等价于不可调度——没有 kubelet 执行 ActiveDeadlineSeconds）。
	// 补池如果缺少单次超时，唯一的 worker 会在这里永久挂死。
	client := fake.NewSimpleClientset()
	indexer := cache.NewIndexer(cache.MetaNamespaceKeyFunc, cache.Indexers{
		cache.NamespaceIndex: cache.MetaNamespaceIndexFunc,
	})
	lister := corelisters.NewPodLister(indexer)
	manager := &Manager{client: client, config: Config{Namespace: "sandboxd-demo"}, podLister: lister}
	pool := &Pool{
		client:        client,
		lister:        lister,
		manager:       manager,
		target:        1,
		createTimeout: 100 * time.Millisecond,
		queue: workqueue.NewTypedRateLimitingQueue(
			workqueue.DefaultTypedControllerRateLimiter[string](),
		),
	}
	defer pool.queue.ShutDown()

	done := make(chan error, 1)
	go func() {
		done <- pool.Reconcile(context.Background())
	}()

	select {
	case err := <-done:
		if err == nil {
			t.Fatal("永不 Ready 的创建应返回错误，而不是被当作成功")
		}
	case <-time.After(5 * time.Second):
		t.Fatal("Reconcile 被未 Ready 的创建永久卡住：补池缺少单次超时")
	}

	// 超时创建的 Pod 必须被清理，不能残留占用 namespace 与池配额。
	pods, err := client.CoreV1().Pods("sandboxd-demo").List(context.Background(), metav1.ListOptions{})
	if err != nil {
		t.Fatal(err)
	}
	if len(pods.Items) != 0 {
		t.Fatalf("超时创建的 Pod 应被删除，残留 %d 个", len(pods.Items))
	}
}

func TestClaimPatchPathMatchesStateLabel(t *testing.T) {
	// claimPatch 是手写 JSON，路径与 types.go 的 LabelState 是两份独立知识；
	// 改 label key 不会编译报错而是静默失效（test/replace 落在不存在路径上），
	// 必须用测试锁死，防止 CAS 语义无声退化。
	escaped := strings.ReplaceAll(LabelState, "/", "~1")
	path := "/metadata/labels/" + escaped
	if !bytes.Contains(claimPatch, []byte(path)) {
		t.Fatalf("claimPatch 未引用 state label 路径 %s，CAS 会静默失效", path)
	}
}

func TestIsClaimConflict(t *testing.T) {
	tests := []struct {
		name string
		err  error
		want bool
	}{
		{
			name: "json patch test 失败返回 422 invalid",
			err: apierrors.NewInvalid(
				schema.GroupKind{Group: "", Kind: "Pod"},
				"sandbox-x",
				field.ErrorList{field.Invalid(field.NewPath("metadata"), nil, "test failed")},
			),
			want: true,
		},
		{
			name: "版本冲突返回 409",
			err: apierrors.NewConflict(
				schema.GroupResource{Group: "", Resource: "pods"},
				"sandbox-x",
				errors.New("the object has been modified"),
			),
			want: true,
		},
		{
			name: "错误文本包含 test failed 时兜底放行",
			err:  errors.New("the server rejected our request: test failed"),
			want: true,
		},
		{
			name: "权限与网络类错误必须中止 Claim",
			err: apierrors.NewForbidden(
				schema.GroupResource{Group: "", Resource: "pods"},
				"sandbox-x",
				errors.New("forbidden"),
			),
			want: false,
		},
		{name: "普通基础设施错误", err: errors.New("connection refused"), want: false},
	}
	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			if got := isClaimConflict(test.err); got != test.want {
				t.Fatalf("isClaimConflict(%v) = %v, want %v", test.err, got, test.want)
			}
		})
	}
}
