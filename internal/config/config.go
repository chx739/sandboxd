package config

import (
	"errors"
	"flag"
	"os"
	"time"

	"k8s.io/client-go/tools/clientcmd"
)

type Config struct {
	Listen        string
	Namespace     string
	Image         string
	RuntimeClass  string
	Kubeconfig    string
	AgentToken    string
	OperatorToken string
	CreateTimeout time.Duration
	ExecTimeout   time.Duration
	PoolSize      int
}

func Parse(args []string) (Config, error) {
	var cfg Config
	flags := flag.NewFlagSet("sandboxd", flag.ContinueOnError)

	flags.StringVar(&cfg.Listen, "listen", "127.0.0.1:8080", "HTTP 监听地址")
	flags.StringVar(&cfg.Namespace, "namespace", "sandboxd-demo", "沙箱 namespace")
	flags.StringVar(&cfg.Image, "image", "curlimages/curl:8.12.1", "沙箱镜像")
	flags.StringVar(&cfg.RuntimeClass, "runtime-class", "gvisor", "Kubernetes RuntimeClass；空值表示默认运行时")
	flags.StringVar(&cfg.Kubeconfig, "kubeconfig", clientcmd.RecommendedHomeFile, "kubeconfig 路径")
	flags.DurationVar(&cfg.CreateTimeout, "create-timeout", 3*time.Minute, "创建沙箱超时")
	flags.DurationVar(&cfg.ExecTimeout, "exec-timeout", 30*time.Second, "单次命令最长执行时间")
	flags.IntVar(&cfg.PoolSize, "pool-size", 2, "预热池 idle Pod 数量")

	if err := flags.Parse(args); err != nil {
		return Config{}, err
	}

	// Token 只从环境变量读取，避免出现在 shell history 和进程参数中。
	cfg.AgentToken = os.Getenv("SANDBOXD_TOKEN")
	cfg.OperatorToken = os.Getenv("SANDBOXD_OPERATOR_TOKEN")
	if cfg.AgentToken == "" {
		return Config{}, errors.New("SANDBOXD_TOKEN 不能为空；服务不允许无 Agent 鉴权启动")
	}
	if cfg.OperatorToken == "" {
		return Config{}, errors.New("SANDBOXD_OPERATOR_TOKEN 不能为空；审批接口不允许无 Operator 鉴权启动")
	}
	if cfg.AgentToken == cfg.OperatorToken {
		return Config{}, errors.New("Agent Token 与 Operator Token 必须不同")
	}
	if cfg.Namespace == "" || cfg.Image == "" {
		return Config{}, errors.New("namespace 和 image 不能为空")
	}
	if cfg.CreateTimeout <= 0 || cfg.ExecTimeout <= 0 {
		return Config{}, errors.New("timeout 必须大于 0")
	}
	if cfg.PoolSize < 0 {
		return Config{}, errors.New("pool-size 不能小于 0")
	}
	return cfg, nil
}
