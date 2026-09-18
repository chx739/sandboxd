package sandbox

import (
	"context"
	"fmt"
	"math/rand"
	"sort"
	"strings"
	"time"

	"github.com/chx739/sandboxd/internal/metrics"
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
	client        kubernetes.Interface
	lister        corelisters.PodLister
	informer      cache.SharedIndexInformer
	queue         workqueue.TypedRateLimitingInterface[string]
	manager       *Manager
	target        int
	createTimeout time.Duration
}

func NewPool(
	client kubernetes.Interface,
	lister corelisters.PodLister,
	informer cache.SharedIndexInformer,
	manager *Manager,
	target int,
	createTimeout time.Duration,
) (*Pool, error) {
	if target < 0 {
		return nil, fmt.Errorf("pool target 不能小于 0")
	}
	if createTimeout <= 0 {
		return nil, fmt.Errorf("pool createTimeout 必须大于 0")
	}

	pool := &Pool{
		client:        client,
		lister:        lister,
		informer:      informer,
		manager:       manager,
		target:        target,
		createTimeout: createTimeout,
		queue: workqueue.NewTypedRateLimitingQueue(
			workqueue.DefaultTypedControllerRateLimiter[string](),
		),
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

	recordPoolSize(pods)
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
			// 每次补池必须单独限时。WaitReady 只对 Failed/Succeeded 提前退出；
			// 不可调度的 Pod 没有节点上的 kubelet 去执行 ActiveDeadlineSeconds，
			// 会永远停在 Pending。而池只有一个 worker，一次无限期的等待就会把
			// 整个对账循环（包括 Failed Pod 清理）永久卡死，直到进程重启。
			createCtx, cancel := context.WithTimeout(ctx, p.createTimeout)
			_, err := p.manager.CreateIdle(createCtx)
			cancel()
			if err != nil {
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
	started := time.Now()
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
			metrics.AcquireDuration.WithLabelValues("pool").Observe(time.Since(started).Seconds())
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
		// JSON Patch test 失败说明候选已被其他请求抢走；缓存仍显示 idle 是正常的。
		if isClaimConflict(patchErr) {
			metrics.ClaimConflicts.Inc()
			continue
		}
		return nil, fmt.Errorf("CAS 认领 Pod %s: %w", candidate.Name, patchErr)
	}

	return p.manager.Create(ctx)
}

// isClaimConflict 判断 CAS 失败是否只是“候选被别人抢走”，可以换下一个候选继续。
// JSON Patch test 失败由 API Server 以 422/409 返回；错误文本匹配只作最后兜底，
// 因为不同版本对 test 失败的 StatusReason 可能不完全一致，宁可多试一个候选，
// 也不能把可恢复的冲突误判成基础设施错误而让整个 Claim 失败。
func isClaimConflict(err error) bool {
	return apierrors.IsInvalid(err) ||
		apierrors.IsConflict(err) ||
		strings.Contains(err.Error(), "test failed")
}

// Release 直接删除而不复用，避免上一个命令留下文件或子进程污染下一次任务。
func (p *Pool) Release(ctx context.Context, id string) error {
	if err := p.manager.Delete(ctx, id); err != nil {
		return err
	}
	p.queue.Add(poolKey)
	return nil
}
