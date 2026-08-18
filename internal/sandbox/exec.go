package sandbox

import (
	"context"
	"errors"
	"fmt"
	"io"
	"net/http"
	"time"

	"github.com/chx739/sandboxd/internal/metrics"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/util/httpstream"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/remotecommand"
	utilexec "k8s.io/client-go/util/exec"
)

// Exec 通过 API Server 的 pods/exec 子资源执行命令。
// 这里发生 WebSocket/SPDY 协议升级，不是一次普通的 HTTP 请求。
func (m *Manager) Exec(
	ctx context.Context,
	podName string,
	command []string,
	stdin io.Reader,
	stdout io.Writer,
	stderr io.Writer,
) (int, error) {
	if len(command) == 0 {
		return -1, errors.New("命令不能为空")
	}
	if m.restConfig == nil {
		return -1, errors.New("rest.Config 不能为空")
	}

	started := time.Now()
	defer func() { metrics.ExecDuration.Observe(time.Since(started).Seconds()) }()

	request := m.client.CoreV1().RESTClient().Post().
		Resource("pods").
		Name(podName).
		Namespace(m.config.Namespace).
		SubResource("exec").
		VersionedParams(&corev1.PodExecOptions{
			Container: "sandbox",
			Command:   command,
			Stdin:     stdin != nil,
			Stdout:    stdout != nil,
			Stderr:    stderr != nil,
			TTY:       false,
		}, scheme.ParameterCodec)

	websocketExecutor, err := remotecommand.NewWebSocketExecutor(
		m.restConfig,
		http.MethodGet,
		request.URL().String(),
	)
	if err != nil {
		return -1, fmt.Errorf("创建 WebSocket executor: %w", err)
	}

	spdyExecutor, err := remotecommand.NewSPDYExecutor(
		m.restConfig,
		http.MethodPost,
		request.URL(),
	)
	if err != nil {
		return -1, fmt.Errorf("创建 SPDY executor: %w", err)
	}

	executor, err := remotecommand.NewFallbackExecutor(
		websocketExecutor,
		spdyExecutor,
		func(streamErr error) bool {
			// 命令返回非零退出码不能 fallback，否则同一命令可能被执行两次。
			return httpstream.IsUpgradeFailure(streamErr) || httpstream.IsHTTPSProxyError(streamErr)
		},
	)
	if err != nil {
		return -1, fmt.Errorf("创建 fallback executor: %w", err)
	}

	err = executor.StreamWithContext(ctx, remotecommand.StreamOptions{
		Stdin:  stdin,
		Stdout: stdout,
		Stderr: stderr,
		Tty:    false,
	})
	if err == nil {
		return 0, nil
	}

	if ctx.Err() != nil {
		metrics.ExecTimeouts.Inc()
		// 超时后进程状态不可信，整个沙箱直接销毁，不放回池中复用。
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = m.deletePod(cleanupCtx, podName)
		return -1, ctx.Err()
	}

	var exitError utilexec.ExitError
	if errors.As(err, &exitError) {
		return exitError.ExitStatus(), err
	}
	return -1, err
}
