package sandbox

import (
	"context"
	"fmt"
	"math/rand"
	"sort"
	"strings"
	"time"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes"
	corelisters "k8s.io/client-go/listers/core/v1"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/util/workqueue"
)

const poolKey = "pool"

var claimPatch = []byte(`[
  {"op":"test","path":"/metadata/labels/sandbox.io~1state","value":"idle"},
  {"op":"replace","path":"/metadata/labels/sandbox.io~1state","value":"busy"}
]`)

type Pool struct {
	client   kubernetes.Interface
	lister   corelisters.PodLister
	informer cache.SharedIndexInformer
	queue    workqueue.TypedRateLimitingInterface[string]
	manager  *Manager
	target   int
}

func NewPool(
	client kubernetes.Interface,
	lister corelisters.PodLister,
	informer cache.SharedIndexInformer,
	manager *Manager,
	target int,
) (*Pool, error) {
	if target < 0 {
		return nil, fmt.Errorf("pool target 不能小于 0")
	}

	pool := &Pool{
		client:   client,
		lister:   lister,
		informer: informer,
		queue: workqueue.NewTypedRateLimitingQueue(
			workqueue.DefaultTypedControllerRateLimiter[string](),
		),
		manager: manager,
		target:  target,
	}

	// handler 不做业务判断，只触发一次基于最新缓存的 reconcile。
	// workqueue 会对同一个 key 去重，事件风暴不会并发执行多份对账逻辑。
	_, err := informer.AddEventHandler(cache.ResourceEventHandlerFuncs{
		AddFunc:    func(any) { pool.queue.Add(poolKey) },
		UpdateFunc: func(any, any) { pool.queue.Add(poolKey) },
		DeleteFunc: func(any) { pool.queue.Add(poolKey) },
	})
	if err != nil {
		return nil, fmt.Errorf("注册 Pool informer handler: %w", err)
	}
	return pool, nil
}

// Run 只启动一个 worker。固定 key 本身也避免同一池被并发 reconcile。
func (p *Pool) Run(ctx context.Context) {
	p.queue.Add(poolKey)
	go func() {
		<-ctx.Done()
		p.queue.ShutDown()
	}()

	for p.processNext(ctx) {
	}
}

func (p *Pool) processNext(ctx context.Context) bool {
	key, shutdown := p.queue.Get()
	if shutdown {
		return false
	}
	defer p.queue.Done(key)

	if err := p.Reconcile(ctx); err != nil {
		if ctx.Err() == nil {
			p.queue.AddRateLimited(key)
		}
		return true
	}
	p.queue.Forget(key)
	return true
}

// Reconcile 完全依据 informer 当前快照计算差额，重复执行不会累计内存计数。
func (p *Pool) Reconcile(ctx context.Context) error {
	pods, err := p.lister.Pods(p.manager.config.Namespace).List(labels.SelectorFromSet(labels.Set{
		LabelManagedBy: ValueManagedBy,
	}))
	if err != nil {
		return fmt.Errorf("列出 Pool Pod: %w", err)
	}

	idle := make([]*corev1.Pod, 0, len(pods))
	for _, pod := range pods {
		if pod.Status.Phase == corev1.PodFailed || pod.Status.Phase == corev1.PodSucceeded {
			if err := p.manager.deletePod(ctx, pod.Name); err != nil {
				return err
			}
			continue
		}
		if pod.DeletionTimestamp == nil && pod.Labels[LabelState] == string(StateIdle) {
			// Pending idle 也计入容量，避免镜像拉取期间反复补 Pod 导致池膨胀。
			idle = append(idle, pod)
		}
	}

	if len(idle) < p.target {
		for range p.target - len(idle) {
			if _, err := p.manager.CreateIdle(ctx); err != nil {
				return fmt.Errorf("补充预热池: %w", err)
			}
		}
		return nil
	}

	if len(idle) > p.target {
		sort.Slice(idle, func(i, j int) bool {
			return idle[i].CreationTimestamp.Before(&idle[j].CreationTimestamp)
		})
		for _, pod := range idle[:len(idle)-p.target] {
			if err := p.manager.deletePod(ctx, pod.Name); err != nil {
				return err
			}
		}
	}
	return nil
}

// Claim 先从 Ready idle 候选中 CAS 认领；缓存为空或候选都冲突时才冷启动。
func (p *Pool) Claim(ctx context.Context) (*Sandbox, error) {
	pods, err := p.lister.Pods(p.manager.config.Namespace).List(labels.SelectorFromSet(labels.Set{
		LabelManagedBy: ValueManagedBy,
		LabelState:     string(StateIdle),
	}))
	if err != nil {
		return nil, fmt.Errorf("列出 idle Pod: %w", err)
	}

	candidates := make([]*corev1.Pod, 0, len(pods))
	for _, pod := range pods {
		if pod.DeletionTimestamp == nil && isPodReady(pod) {
			candidates = append(candidates, pod)
		}
	}
	// 打乱候选可降低多个请求总是撞到同一个 Pod 的概率；正确性仍由 API Server CAS 保证。
	random := rand.New(rand.NewSource(time.Now().UnixNano()))
	random.Shuffle(len(candidates), func(i, j int) {
		candidates[i], candidates[j] = candidates[j], candidates[i]
	})

	for _, candidate := range candidates {
		claimed, patchErr := p.client.CoreV1().Pods(p.manager.config.Namespace).Patch(
			ctx,
			candidate.Name,
			types.JSONPatchType,
			claimPatch,
			metav1.PatchOptions{},
		)
		if patchErr == nil {
			p.queue.Add(poolKey)
			return &Sandbox{
				ID:        claimed.Labels[LabelID],
				PodName:   claimed.Name,
				Namespace: claimed.Namespace,
				State:     StateBusy,
				CreatedAt: claimed.CreationTimestamp.Time,
				Source:    "pool",
			}, nil
		}
		// JSON Patch test 失败通常是 422；缓存仍显示 idle，但对象已被其他请求认领。
		if apierrors.IsInvalid(patchErr) || apierrors.IsConflict(patchErr) || strings.Contains(patchErr.Error(), "test failed") {
			continue
		}
		return nil, fmt.Errorf("CAS 认领 Pod %s: %w", candidate.Name, patchErr)
	}

	return p.manager.Create(ctx)
}

// Release 直接删除而不复用，避免上一个命令留下文件或子进程污染下一次任务。
func (p *Pool) Release(ctx context.Context, id string) error {
	if err := p.manager.Delete(ctx, id); err != nil {
		return err
	}
	p.queue.Add(poolKey)
	return nil
}
