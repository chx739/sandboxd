package main

import (
	"context"
	"errors"
	"log"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/chx739/sandboxd/internal/api"
	"github.com/chx739/sandboxd/internal/config"
	"github.com/chx739/sandboxd/internal/sandbox"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/client-go/informers"
	"k8s.io/client-go/kubernetes"
	"k8s.io/client-go/tools/cache"
	"k8s.io/client-go/tools/clientcmd"
)

func main() {
	cfg, err := config.Parse(os.Args[1:])
	if err != nil {
		log.Fatal(err)
	}

	restConfig, err := clientcmd.BuildConfigFromFlags("", cfg.Kubeconfig)
	if err != nil {
		log.Fatalf("读取 kubeconfig: %v", err)
	}
	restConfig.UserAgent = "sandboxd"

	client, err := kubernetes.NewForConfig(restConfig)
	if err != nil {
		log.Fatalf("创建 Kubernetes client: %v", err)
	}

	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	selector := labels.Set{sandbox.LabelManagedBy: sandbox.ValueManagedBy}.String()
	factory := informers.NewSharedInformerFactoryWithOptions(
		client,
		30*time.Minute,
		informers.WithNamespace(cfg.Namespace),
		informers.WithTweakListOptions(func(options *metav1.ListOptions) {
			options.LabelSelector = selector
		}),
	)
	podInformer := factory.Core().V1().Pods()
	sharedPodInformer := podInformer.Informer()
	factory.Start(ctx.Done())
	if !cache.WaitForCacheSync(ctx.Done(), sharedPodInformer.HasSynced) {
		log.Fatal("等待 Pod informer 缓存同步失败")
	}

	manager := sandbox.NewManager(client, restConfig, sandbox.Config{
		Namespace:    cfg.Namespace,
		Image:        cfg.Image,
		RuntimeClass: cfg.RuntimeClass,
	}, podInformer.Lister())
	pool, err := sandbox.NewPool(
		client,
		podInformer.Lister(),
		sharedPodInformer,
		manager,
		cfg.PoolSize,
	)
	if err != nil {
		log.Fatalf("创建预热池: %v", err)
	}
	go pool.Run(ctx)

	apiServer := api.NewServer(manager, pool, cfg.Token, cfg.CreateTimeout, cfg.ExecTimeout)
	httpServer := &http.Server{
		Addr:              cfg.Listen,
		Handler:           apiServer.Handler(),
		ReadHeaderTimeout: 5 * time.Second,
		IdleTimeout:       60 * time.Second,
	}

	go func() {
		<-ctx.Done()
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		_ = httpServer.Shutdown(shutdownCtx)
	}()

	log.Printf("sandboxd listening on http://%s", cfg.Listen)
	if err := httpServer.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatal(err)
	}
}
